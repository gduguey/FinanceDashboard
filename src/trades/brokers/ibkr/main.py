"""IBKR orchestration: fetch a single Flex Query, parse it, and merge it into the local ledger cache."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.brokers.ibkr.api import fetch_flex_statement, parse_statement, save_raw_statement
from trades.brokers.ibkr.preprocessing import statement_to_ledger
from trades.models import LedgerEvent
from trades.utils.io_utils import write_csv_atomic

if TYPE_CHECKING:
    from datetime import date

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
    return (
        pl
        .concat([existing, new], how="vertical")
        .unique(subset="event_id", keep="last")
        .sort("event_datetime", "symbol", "event_id")
    )


def load_ledger(config: AppConfig) -> pl.DataFrame:
    """Load the full event ledger.

    `meta` round-trips through the CSV as JSON (a dict has no native CSV
    type). `*_id` columns are forced to string dtype, or an all-numeric ID
    like "9001" would round-trip as an integer and silently break dedup
    against the str "9001" freshly parsed from XML.

    Parameters
    ----------
    config
        Application configuration; `config.ibkr.ledger_csv_path` is read.

    Returns
    -------
    polars.DataFrame
        The ledger, or an empty frame if it has never been synced.
    """
    path = config.ibkr.ledger_csv_path
    if not path.exists():
        return pl.DataFrame(schema=LedgerEvent.polars_schema)
    ledger = pl.read_csv(path, schema_overrides={"event_id": pl.Utf8}, try_parse_dates=True)
    return ledger.with_columns(pl.col("meta").map_elements(json.loads, return_dtype=pl.Object))


def _write_ledger(ledger: pl.DataFrame, config: AppConfig) -> None:
    serialized = ledger.with_columns(pl.col("meta").map_elements(json.dumps, return_dtype=pl.Utf8))
    write_csv_atomic(serialized, config.ibkr.ledger_csv_path)


def sync_ibkr_account(credentials: IbkrFlexCredentials, config: AppConfig) -> IbkrSyncResult:
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
        Application configuration; `config.ibkr` is read.

    Returns
    -------
    IbkrSyncResult
        What was pulled and how the ledger changed.

    Raises
    ------
    TradeHistoryGapError
        If this pull's coverage window doesn't connect to what's already cached.
    """
    xml_text = fetch_flex_statement(credentials, config)
    save_raw_statement(xml_text, datetime.now(), config)  # noqa: DTZ005
    statement = parse_statement(xml_text)

    existing_ledger = load_ledger(config)
    if not existing_ledger.is_empty():
        last_covered_date = cast("datetime", existing_ledger["event_datetime"].max()).date()
        if last_covered_date < statement.from_date:
            message = (
                f"Cached ledger ends {last_covered_date}, but this pull only covers from "
                f"{statement.from_date} onward. Backfill the gap with a custom-date-range Flex "
                "Query before syncing again, or events in between will be lost for good."
            )
            raise TradeHistoryGapError(message)

    merged_ledger = _merge_ledger(existing_ledger, statement_to_ledger(statement, config))
    _write_ledger(merged_ledger, config)

    return IbkrSyncResult(
        pulled_at=statement.when_generated,
        statement_from_date=statement.from_date,
        statement_to_date=statement.to_date,
        new_event_count=len(merged_ledger) - len(existing_ledger),
        total_event_count=len(merged_ledger),
    )


def rebuild_from_raw_statements(config: AppConfig) -> IbkrSyncResult:
    """Recompute the ledger from every archived raw statement.

    Discards whatever ledger is currently on disk. Use this to recover if
    the derived ledger is ever wrong or corrupted. Does not gap-check: it
    faithfully reconstructs from whatever was archived, which is the same
    coverage `sync_ibkr_account` already verified as gap-free when each
    statement was originally fetched.

    Parameters
    ----------
    config
        Application configuration; `config.ibkr` is read.

    Returns
    -------
    IbkrSyncResult
        The coverage window of the rebuilt ledger.

    Raises
    ------
    FileNotFoundError
        If no raw statements have ever been archived.
    """
    raw_paths = sorted(config.ibkr.raw_statement_dir.glob("*.xml"))
    if not raw_paths:
        message = f"No archived raw statements under {config.ibkr.raw_statement_dir}"
        raise FileNotFoundError(message)

    statements: list[ParsedStatement] = sorted(
        (parse_statement(path.read_text(encoding="utf-8")) for path in raw_paths),
        key=lambda statement: statement.when_generated,
    )

    ledger = _merge_ledger(
        pl.DataFrame(schema=LedgerEvent.polars_schema),
        pl.concat([statement_to_ledger(statement, config) for statement in statements], how="vertical"),
    )
    _write_ledger(ledger, config)

    return IbkrSyncResult(
        pulled_at=statements[-1].when_generated,
        statement_from_date=min(statement.from_date for statement in statements),
        statement_to_date=max(statement.to_date for statement in statements),
        new_event_count=len(ledger),
        total_event_count=len(ledger),
    )
