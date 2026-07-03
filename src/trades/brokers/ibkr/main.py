"""IBKR Orchestration: fetch a single Flex Query, parse it, and merge it into the local ledger cache.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from trades.brokers.ibkr.api import (
    fetch_flex_statement,
    parse_statement,
    save_raw_statement,
)
from trades.brokers.ibkr.preprocessing import statement_to_ledger
from trades.config import IbkrFlexApiConfig, IbkrFlexCredentials
from trades.models import LedgerEvent


@dataclass(frozen=True)
class IbkrSyncResult:
    """What happened during one `sync_ibkr_account` call."""

    pulled_at: datetime
    statement_from_date: date
    statement_to_date: date
    new_event_count: int
    total_event_count: int


class TradeHistoryGapError(ValueError):
    """This pull doesn't connect to the end of the cached trade history."""


def load_ledger(config: IbkrFlexApiConfig) -> pd.DataFrame:
    """Loads the full event ledger.

    `meta` round-trips through the CSV as JSON (a dict has no native CSV
    type). `*_id` columns are forced to string dtype, or an all-numeric ID
    like "9001" would round-trip as int64 and silently break dedup against
    the str "9001" freshly parsed from XML.
    """
    path = config.ledger_csv_path
    columns = list(LedgerEvent.model_fields)
    if not path.exists():
        return pd.DataFrame(columns=columns)
    id_dtypes = {c: str for c in columns if c.endswith("_id")}
    df = pd.read_csv(path, parse_dates=["event_datetime"], dtype=id_dtypes) # pyright: ignore[reportArgumentType]
    df["meta"] = df["meta"].apply(json.loads)
    return df


def sync_ibkr_account(
    credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig
) -> IbkrSyncResult:
    """Pull the configured Flex Query once and bring the local ledger cache
    in sync: append newly seen events (deduped by `event_id`) from that single fetched statement.

    Raises `TradeHistoryGapError` if this pull's coverage window doesn't
    connect to what's already cached. The fetched statement is archived to disk before that check 
    runs, so even a raised error never loses the data that was just pulled.
    """
    xml_text = fetch_flex_statement(credentials, config)
    save_raw_statement(xml_text, datetime.now(), config)
    statement = parse_statement(xml_text)

    existing_ledger = load_ledger(config)
    if not existing_ledger.empty:
        last_covered_date = existing_ledger["event_datetime"].max().date()
        if last_covered_date < statement.from_date:
            raise TradeHistoryGapError(
                f"Cached ledger ends {last_covered_date}, but this pull only covers from "
                f"{statement.from_date} onward. Backfill the gap with a custom-date-range Flex "
                "Query before syncing again, or events in between will be lost for good."
            )

    merged_ledger_df = (pd.concat([existing_ledger, statement_to_ledger(statement, config)], ignore_index=True)
        .drop_duplicates(subset="event_id", keep="last")
        .sort_values(["event_datetime", "symbol", "event_id"])
        .reset_index(drop=True)
    )

    out_df = merged_ledger_df.assign(meta=merged_ledger_df["meta"].apply(json.dumps))
    out_path = config.ledger_csv_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    return IbkrSyncResult(
        pulled_at=statement.when_generated,
        statement_from_date=statement.from_date,
        statement_to_date=statement.to_date,
        new_event_count=len(merged_ledger_df) - len(existing_ledger),
        total_event_count=len(merged_ledger_df),
    )


def rebuild_from_raw_statements(config: IbkrFlexApiConfig) -> IbkrSyncResult:
    """Recompute the Ledger from every archived raw statement, discarding
    whatever Ledger is currently on disk. Use this to recover if the derived
    ledger is ever wrong or corrupted.

    Does not gap-check: it faithfully reconstructs from whatever was
    archived, which is the same coverage `sync_ibkr_account` already
    verified as gap-free when each statement was originally fetched.
    """
    raw_paths = sorted(config.raw_statement_dir.glob("*.xml"))
    if not raw_paths:
        raise FileNotFoundError(f"No archived raw statements under {config.raw_statement_dir}")

    statements = sorted(
        (parse_statement(path.read_text(encoding="utf-8")) for path in raw_paths),
        key=lambda statement: statement.when_generated,
    )

    ledger_df = pd.DataFrame(columns=list(LedgerEvent.model_fields))
    for statement in statements:
        ledger_df = (pd.concat([ledger_df, statement_to_ledger(statement, config)], ignore_index=True)
            .drop_duplicates(subset="event_id", keep="last")
            .sort_values(["event_datetime", "symbol", "event_id"])
            .reset_index(drop=True)
        )

    out_df = ledger_df.assign(meta=ledger_df["meta"].apply(json.dumps))
    out_path = config.ledger_csv_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    return IbkrSyncResult(
        pulled_at=statements[-1].when_generated,
        statement_from_date=min(statement.from_date for statement in statements),
        statement_to_date=max(statement.to_date for statement in statements),
        new_event_count=len(ledger_df),
        total_event_count=len(ledger_df),
    )
