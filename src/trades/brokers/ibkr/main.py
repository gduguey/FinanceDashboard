"""IBKR orchestration: fetch a single Flex Query, parse it, and merge it into the local ledger cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import polars as pl

import trades.db as tdb
from db.base import ensure_reference_rows, merge_by_natural_key
from db.money import to_analytics_float
from trades.brokers.ibkr.api import fetch_flex_statement, parse_statement, save_raw_statement
from trades.brokers.ibkr.preprocessing import statement_to_ledger
from trades.models import LedgerEvent
from trades.utils.statement_archive import StatementArchive

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable
    from datetime import date

    from sqlalchemy.orm import Session

    from trades.brokers.ibkr.api import ParsedStatement
    from trades.config import AppConfig, IbkrFlexCredentials


@dataclass(frozen=True)
class IbkrSyncResult:
    """What happened during one `sync_ibkr_account` call."""

    pulled_at: datetime
    statement_from_date: date
    statement_to_date: date
    new_event_count: int
    total_event_count: int


class TradeHistoryGapError(ValueError):
    """A pull doesn't connect to the end of the cached trade history."""


def _merge_ledger(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Combine `existing` and `new` events, deduped by `event_id`, sorted chronologically.

    Returns
    -------
    polars.DataFrame
    """
    return (
        pl
        .concat([existing, new], how="vertical")
        .unique(subset="event_id", keep="last")
        .sort("event_datetime", "symbol", "event_id")
    )


_DEFAULT_CONNECTION_ID = "ibkr"
"""The one broker connection every event belongs to today.

Credentials themselves are already per-user (see
`trades.brokers.ibkr.credentials`, backed by Postgres, never `.env`), but
there's no multi-connection UI yet — this app supports one IBKR connection
per user, so every `LedgerEvent` foreign-keys against this one fixed
`BrokerConnection` row, created on first write if it doesn't exist yet.
When a user can have more than one broker connection, this becomes a real
id chosen at connection-creation time instead of a constant.
"""


def load_ledger(session: Session, user_id: uuid.UUID) -> pl.DataFrame:
    """Load the full event ledger.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger to load.

    Returns
    -------
    polars.DataFrame
        Shaped exactly like `LedgerEvent.polars_schema` — every other
        ledger and dashboard module depends on that shape, not on how it's
        actually stored. An empty frame if it has never been synced.
    """
    rows = (
        session
        .query(tdb.LedgerEvent, tdb.LedgerEventTradeDetails)
        .outerjoin(tdb.LedgerEventTradeDetails, tdb.LedgerEventTradeDetails.ledger_event_id == tdb.LedgerEvent.id)
        .filter(tdb.LedgerEvent.user_id == user_id)
        .all()
    )
    if not rows:
        return pl.DataFrame(schema=LedgerEvent.polars_schema)
    records = [
        {
            "event_id": event.natural_key,
            "event_datetime": event.event_datetime,
            "symbol": event.symbol,
            "event_type": event.event_type,
            # Crossing into the float analytics projection — see `db.money.to_analytics_float`.
            "shares": to_analytics_float(details.shares) if details is not None else None,
            "price": to_analytics_float(details.price) if details is not None else None,
            "amount": to_analytics_float(event.amount),
            "currency": event.currency,
            "meta": event.meta,
        }
        for event, details in rows
    ]
    return pl.DataFrame(records, schema=LedgerEvent.polars_schema).sort("event_datetime", "symbol", "event_id")


def _write_ledger(ledger: pl.DataFrame, session: Session, user_id: uuid.UUID) -> None:
    """Persist the full event ledger, overwriting whatever was saved before.

    Unlike `accounting.importers.ingest._write_ledger`, this is a plain
    delete-all-then-reinsert — nothing else in this schema foreign-keys
    into `ledger_events`, so there's no risk of deleting a row still
    referenced elsewhere (see `DATABASE_SCHEMA.md`).

    Parameters
    ----------
    ledger
        The full ledger to persist, shaped like `LedgerEvent.polars_schema`.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose ledger this is.
    """
    connection_ids = merge_by_natural_key(
        session,
        tdb.BrokerConnection,
        user_id,
        [tdb.BrokerConnection(user_id=user_id, natural_key=_DEFAULT_CONNECTION_ID, broker="ibkr")],
    )
    connection_id = connection_ids[_DEFAULT_CONNECTION_ID]

    # ON DELETE CASCADE on ledger_event_trade_details.ledger_event_id means this alone
    # also removes every deleted event's trade details — no separate delete needed.
    session.query(tdb.LedgerEvent).filter_by(user_id=user_id).delete()

    rows = ledger.to_dicts()
    # `ledger_events.symbol` references `trades.securities` now, and symbols
    # arrive dynamically — this statement is the first time this database has
    # heard of whatever the user bought since the last sync — so the
    # instrument is created before the events that name it. Whole batch in one
    # statement, so a thousand-event ledger costs one round trip.
    ensure_reference_rows(session, tdb.Security, [row["symbol"] for row in rows])
    new_events = [
        tdb.LedgerEvent(
            user_id=user_id,
            natural_key=row["event_id"],
            connection_id=connection_id,
            event_datetime=row["event_datetime"],
            symbol=row["symbol"],
            event_type=row["event_type"],
            amount=row["amount"],
            currency=row["currency"],
            meta=row["meta"],
        )
        for row in rows
    ]
    session.add_all(new_events)
    # Flushed before the trade details, whose `ledger_event_id` is both their
    # own primary key and a foreign key into the event — so it is the id
    # `uuid7()` just minted, read off the flushed instance.
    session.flush()
    session.add_all(
        tdb.LedgerEventTradeDetails(ledger_event_id=event.id, user_id=user_id, shares=row["shares"], price=row["price"])
        for event, row in zip(new_events, rows, strict=True)
        if row["shares"] is not None
    )
    session.commit()


def sync_ibkr_account(
    credentials: IbkrFlexCredentials,
    config: AppConfig,
    session: Session,
    user_id: uuid.UUID,
    on_progress: Callable[[str, float], None] | None = None,
) -> IbkrSyncResult:
    """Pull the configured Flex Query once and bring the local ledger cache in sync.

    Appends newly seen events (deduped by `event_id`) from the single
    fetched statement. The fetched statement is archived to disk before
    the gap check runs, so a raised error never loses the data that was
    just pulled.

    Parameters
    ----------
    credentials
        The IBKR Flex Web Service token and query id.
    config
        Application configuration; `config.ibkr` is used for the raw
        statement archive (which stays on disk/R2, not Postgres).
    session
        An open database session.
    user_id
        Whose ledger this is.
    on_progress
        Called with a short step description and a 0-100 percentage as the
        pull proceeds, for a live sync-progress display. Optional.

    Returns
    -------
    IbkrSyncResult
        What was pulled and how the ledger changed.

    Raises
    ------
    TradeHistoryGapError
        If this pull's coverage window doesn't connect to what's already cached.
    """
    xml_text = fetch_flex_statement(credentials, config, on_progress)
    if on_progress:
        on_progress("Parsing statement", 45.0)
    save_raw_statement(xml_text, datetime.now(UTC).replace(tzinfo=None), config, user_id)
    statement = parse_statement(xml_text)

    existing_ledger = load_ledger(session, user_id=user_id)
    if not existing_ledger.is_empty():
        last_covered_date = cast("datetime", existing_ledger["event_datetime"].max()).date()
        if last_covered_date < statement.from_date:
            message = (
                f"Cached ledger ends {last_covered_date}, but this pull only covers from "
                f"{statement.from_date} onward. Backfill the gap with a custom-date-range Flex "
                "Query before syncing again, or events in between will be lost for good."
            )
            raise TradeHistoryGapError(message)

    if on_progress:
        on_progress("Merging into ledger", 55.0)
    merged_ledger = _merge_ledger(existing_ledger, statement_to_ledger(statement, config))
    _write_ledger(merged_ledger, session, user_id=user_id)

    return IbkrSyncResult(
        pulled_at=statement.when_generated,
        statement_from_date=statement.from_date,
        statement_to_date=statement.to_date,
        new_event_count=len(merged_ledger) - len(existing_ledger),
        total_event_count=len(merged_ledger),
    )


def rebuild_from_raw_statements(config: AppConfig, session: Session, user_id: uuid.UUID) -> IbkrSyncResult:
    """Recompute the ledger from every archived raw statement.

    Discards whatever ledger is currently persisted. Use this to recover if
    the derived ledger is ever wrong or corrupted. Does not gap-check: i
    faithfully reconstructs from whatever was archived, which is the same
    coverage `sync_ibkr_account` already verified as gap-free when each
    statement was originally fetched.

    Parameters
    ----------
    config
        Application configuration; `config.ibkr` is read.
    session
        An open database session.
    user_id
        Whose ledger this is.

    Returns
    -------
    IbkrSyncResult
        The coverage window of the rebuilt ledger.

    Raises
    ------
    FileNotFoundError
        If no raw statements have ever been archived.
    """
    archive = StatementArchive(config.ibkr.raw_statement_dir, f"statements/{user_id}/ibkr")
    relative_paths = archive.list_relative_paths("*.xml")
    if not relative_paths:
        message = (
            f"No archived raw statements found (checked {archive.remote_prefix!r} on R2, "
            f"else {config.ibkr.raw_statement_dir})"
        )
        raise FileNotFoundError(message)

    statements: list[ParsedStatement] = sorted(
        (parse_statement(archive.read(relative_path).decode("utf-8")) for relative_path in relative_paths),
        key=lambda statement: statement.when_generated,
    )

    ledger = _merge_ledger(
        pl.DataFrame(schema=LedgerEvent.polars_schema),
        pl.concat([statement_to_ledger(statement, config) for statement in statements], how="vertical"),
    )
    _write_ledger(ledger, session, user_id=user_id)

    return IbkrSyncResult(
        pulled_at=statements[-1].when_generated,
        statement_from_date=min(statement.from_date for statement in statements),
        statement_to_date=max(statement.to_date for statement in statements),
        new_event_count=len(ledger),
        total_event_count=len(ledger),
    )
