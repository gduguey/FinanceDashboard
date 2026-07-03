"""IBKR Flex Web Service: fetch the "Trade History API" Flex Query (Cash
Report + Open Positions + Trades) and keep a local, append-only cache of it.

See docs/ibkr_flex_api.md for how the two-step SendRequest/GetStatement
protocol and its error codes work. Three things make the caching here more
than "write what we fetched":

- The Flex Query's period (currently "Last 365 Calendar Days", configured
  in IBKR's UI, not by this code) bounds every pull to a window — a query
  reconfigured to something narrower later, or a pull missed for longer
  than the window covers, can leave a real gap. So `sync_ibkr_account`
  checks that this pull's coverage connects to what's already cached, and
  raises `TradeHistoryGapError` rather than silently leaving days unrecorded.
- Trades are deduplicated by IBKR's `transactionID` (stable, always present)
  so re-running a sync is a no-op wherever it overlaps a previous pull.
  Position and cash rows are *not* deduplicated — they're an intentional
  time series of snapshots, one batch per pull, each tagged with when IBKR
  generated that statement.
- Every raw fetched statement is archived verbatim, forever, under
  `cache_dir/raw_statements/` *before* any parsing or merging is attempted.
  `trades.csv`/`position_snapshots.csv`/`cash_snapshots.csv` are a
  derived, rebuildable cache of that archive, not the only copy of the
  data — see `rebuild_from_raw_statements`. A bug in the merge/parse logic
  can be fixed and replayed; it can't destroy history that's only ever
  overwritten in place.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from pydantic import BaseModel

from trades.config import IbkrFlexApiConfig, IbkrFlexCredentials
from trades.models import IbkrCashBalance, IbkrPosition, IbkrTrade

# IBKR's own documented Flex Web Service v3 error codes (docs/ibkr_flex_api.md)
# — protocol facts, not tunable parameters.
_RETRYABLE_GENERATING_CODES = frozenset(
    {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1019", "1021"}
)
_RETRYABLE_THROTTLED_CODES = frozenset({"1018"})

_TRADE_COLUMNS = list(IbkrTrade.model_fields)
_POSITION_COLUMNS = ["pulled_at", *IbkrPosition.model_fields]
_CASH_COLUMNS = ["pulled_at", *IbkrCashBalance.model_fields]


class FlexApiError(RuntimeError):
    """The Flex Web Service returned a non-retryable error code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"IBKR Flex API error {code}: {message}")


class TradeHistoryGapError(ValueError):
    """This pull doesn't connect to the end of the cached trade history."""


@dataclass(frozen=True)
class IbkrSyncResult:
    """What happened during one `sync_ibkr_account` call."""

    pulled_at: datetime
    statement_from_date: date
    statement_to_date: date
    new_trade_count: int
    total_trade_count: int


@dataclass(frozen=True)
class _ParsedStatement:
    from_date: date
    to_date: date
    when_generated: datetime
    trades: list[IbkrTrade]
    positions: list[IbkrPosition]
    cash_balances: list[IbkrCashBalance]


# ---------------------------------------------------------------------------
# Network: the two-step SendRequest / GetStatement protocol
# ---------------------------------------------------------------------------


def _send_flex_request(
    credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig
) -> tuple[str, str]:
    """Step 1: exchange the query ID for a reference code and statement URL."""
    response = requests.get(
        config.send_request_url,
        params={"v": "3", "t": credentials.token.get_secret_value(), "q": credentials.query_id},
        headers=config.request_headers,
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    if root.findtext("Status") != "Success":
        raise FlexApiError(
            root.findtext("ErrorCode", "unknown"), root.findtext("ErrorMessage", response.text)
        )
    reference_code = root.findtext("ReferenceCode")
    if not reference_code:
        raise FlexApiError("unknown", "SendRequest succeeded but returned no ReferenceCode")
    return reference_code, root.findtext("Url") or config.fallback_statement_url


def _poll_flex_statement(
    reference_code: str,
    statement_url: str,
    credentials: IbkrFlexCredentials,
    config: IbkrFlexApiConfig,
) -> str:
    """Step 2: poll until the statement is ready, honoring IBKR's documented
    retry codes; raises on any other error or after `max_poll_attempts`."""
    for _ in range(config.max_poll_attempts):
        response = requests.get(
            statement_url,
            params={"v": "3", "t": credentials.token.get_secret_value(), "q": reference_code},
            headers=config.request_headers,
            timeout=config.request_timeout_seconds,
        )
        response.raise_for_status()
        if "<FlexQueryResponse" in response.text:
            return response.text

        root = ET.fromstring(response.text)
        code = root.findtext("ErrorCode", "")
        if code in _RETRYABLE_GENERATING_CODES:
            time.sleep(config.server_busy_retry_seconds)
        elif code in _RETRYABLE_THROTTLED_CODES:
            time.sleep(config.throttled_retry_seconds)
        else:
            raise FlexApiError(code or "unknown", root.findtext("ErrorMessage", response.text))
    raise FlexApiError("timeout", f"Statement not ready after {config.max_poll_attempts} attempts")


def fetch_flex_statement(credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig) -> str:
    """The one network entrypoint: run the full SendRequest -> GetStatement
    exchange and return the raw `FlexQueryResponse` XML."""
    reference_code, statement_url = _send_flex_request(credentials, config)
    return _poll_flex_statement(reference_code, statement_url, credentials, config)


# ---------------------------------------------------------------------------
# Parsing: raw XML -> validated pydantic rows (no I/O below this line)
# ---------------------------------------------------------------------------


def _parse_when_generated(value: str) -> datetime:
    """`whenGenerated` is "yyyy-MM-dd HH:mm:ss" (per the query's configured
    date/time format) followed by a timezone abbreviation IBKR always
    appends (e.g. "EDT") regardless of that setting. Nothing else in this
    codebase tracks timezones, so the abbreviation is dropped rather than
    resolved to a UTC offset — `when_generated` is a naive local timestamp."""
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


def _parse_statement(xml_text: str) -> _ParsedStatement:
    root = ET.fromstring(xml_text)
    statement = root.find(".//FlexStatement")
    if statement is None:
        raise FlexApiError("unknown", "FlexQueryResponse XML has no <FlexStatement> element")
    return _ParsedStatement(
        from_date=datetime.strptime(statement.attrib["fromDate"], "%Y-%m-%d").date(),
        to_date=datetime.strptime(statement.attrib["toDate"], "%Y-%m-%d").date(),
        when_generated=_parse_when_generated(statement.attrib["whenGenerated"]),
        trades=[IbkrTrade.model_validate(el.attrib) for el in root.iter("Trade")],
        positions=[IbkrPosition.model_validate(el.attrib) for el in root.iter("OpenPosition")],
        cash_balances=[
            IbkrCashBalance.model_validate(el.attrib) for el in root.iter("CashReportCurrency")
        ],
    )


def _has_uncovered_weekday_gap(last_covered_date: date, new_from_date: date) -> bool:
    """True if a weekday between the two dates would go unrecorded. A
    holiday can still trip this (a false positive), which just costs a
    manual double-check — never a silently missed trade."""
    cursor = last_covered_date + timedelta(days=1)
    while cursor < new_from_date:
        if cursor.weekday() < 5:
            return True
        cursor += timedelta(days=1)
    return False


# ---------------------------------------------------------------------------
# Local cache: read/write the three CSVs under config.cache_dir
# ---------------------------------------------------------------------------


def _read_csv_or_empty(
    path: Path, columns: list[str], date_columns: Sequence[str] = ()
) -> pd.DataFrame:
    """`*_id` columns (transaction_id, trade_id, account_id) are forced to
    stay string dtype on read — otherwise an all-numeric ID column like
    "9001" round-trips through CSV as int64, and comparing it against the
    str "9001" freshly parsed from XML would silently break dedup."""
    if not path.exists():
        return pd.DataFrame(columns=columns)
    id_dtypes = {c: str for c in columns if c.endswith("_id")}
    return pd.read_csv(path, parse_dates=list(date_columns), dtype=id_dtypes)


def _atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".csv.tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)  # atomic rename on the same filesystem


def _raw_statement_dir(config: IbkrFlexApiConfig) -> Path:
    return config.cache_dir / "raw_statements"


def _save_raw_statement(xml_text: str, received_at: datetime, config: IbkrFlexApiConfig) -> Path:
    """Archive the exact bytes IBKR returned, before any parsing is
    attempted, so a parsing or merge bug can never lose a fetched statement.
    Files are never overwritten: a same-second collision (e.g. two calls in
    a fast test loop) gets a numeric suffix instead of clobbering the first."""
    raw_dir = _raw_statement_dir(config)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{received_at:%Y%m%dT%H%M%S}.xml"
    suffix = 1
    while path.exists():
        path = raw_dir / f"{received_at:%Y%m%dT%H%M%S}-{suffix}.xml"
        suffix += 1
    path.write_text(xml_text, encoding="utf-8")
    return path


def _parse_raw_statement_filename(path: Path) -> datetime:
    stem = path.stem.split("-")[0]  # strip the "-{suffix}" same-second collision tag, if any
    return datetime.strptime(stem, "%Y%m%dT%H%M%S")


def last_synced_at(config: IbkrFlexApiConfig) -> datetime | None:
    """The real local timestamp of the most recent successful sync, read from
    the `raw_statements/` archive's filenames (named by actual receive time —
    see `_save_raw_statement`). Deliberately not `pulled_at` in
    `position_snapshots.csv`/`cash_snapshots.csv`, which is IBKR's own
    `whenGenerated` for the statement, not local wall-clock time (see
    docs/ibkr_flex_api.md) — it can stay frozen across multiple same-day
    pulls and would misreport a sync that just ran as hours stale.

    None if nothing has ever been synced.
    """
    raw_paths = list(_raw_statement_dir(config).glob("*.xml"))
    if not raw_paths:
        return None
    return max(_parse_raw_statement_filename(path) for path in raw_paths)


def _trades_frame(trades: list[IbkrTrade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    df = pd.DataFrame([t.model_dump() for t in trades])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def _snapshot_frame(
    records: Sequence[BaseModel], columns: list[str], pulled_at: datetime
) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([{"pulled_at": pulled_at, **r.model_dump()} for r in records])


def _merge_trade_history(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    return (
        pd.concat([existing, new], ignore_index=True)
        .drop_duplicates(subset="transaction_id", keep="last")
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )


def _append_snapshot(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Snapshots are a time series, not a deduplicated set — every pull adds
    a full new batch, tagged with its own `pulled_at`."""
    return pd.concat([existing, new], ignore_index=True)


def load_trade_history(config: IbkrFlexApiConfig) -> pd.DataFrame:
    return _read_csv_or_empty(
        config.cache_dir / "trades.csv", _TRADE_COLUMNS, date_columns=["trade_date"]
    )


def load_position_snapshots(config: IbkrFlexApiConfig) -> pd.DataFrame:
    return _read_csv_or_empty(
        config.cache_dir / "position_snapshots.csv",
        _POSITION_COLUMNS,
        date_columns=["pulled_at", "report_date"],
    )


def load_cash_snapshots(config: IbkrFlexApiConfig) -> pd.DataFrame:
    return _read_csv_or_empty(
        config.cache_dir / "cash_snapshots.csv",
        _CASH_COLUMNS,
        date_columns=["pulled_at", "from_date", "to_date"],
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def sync_ibkr_account(
    credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig
) -> IbkrSyncResult:
    """Pull the configured Flex Query once and bring the local cache in
    sync: append newly seen trades (deduped by transaction ID) and record a
    new position + cash snapshot, all from that single fetched statement.

    Raises `TradeHistoryGapError` if this pull's coverage window doesn't
    connect to what's already cached (see module docstring). The fetched
    statement is archived to disk before that check runs, so even a raised
    error never loses the data that was just pulled.
    """
    xml_text = fetch_flex_statement(credentials, config)
    _save_raw_statement(xml_text, datetime.now(), config)
    statement = _parse_statement(xml_text)

    existing_trades = load_trade_history(config)
    if not existing_trades.empty:
        last_covered_date = existing_trades["trade_date"].max().date()
        if _has_uncovered_weekday_gap(last_covered_date, statement.from_date):
            raise TradeHistoryGapError(
                f"Cached trade history ends {last_covered_date}, but this pull only covers "
                f"from {statement.from_date} onward. Backfill the gap with a custom-date-range "
                "Flex Query before syncing again, or trades in between will be lost for good."
            )

    trades_df = _merge_trade_history(existing_trades, _trades_frame(statement.trades))
    _atomic_write_csv(config.cache_dir / "trades.csv", trades_df)

    positions_df = _append_snapshot(
        load_position_snapshots(config),
        _snapshot_frame(statement.positions, _POSITION_COLUMNS, statement.when_generated),
    )
    _atomic_write_csv(config.cache_dir / "position_snapshots.csv", positions_df)

    cash_df = _append_snapshot(
        load_cash_snapshots(config),
        _snapshot_frame(statement.cash_balances, _CASH_COLUMNS, statement.when_generated),
    )
    _atomic_write_csv(config.cache_dir / "cash_snapshots.csv", cash_df)

    return IbkrSyncResult(
        pulled_at=statement.when_generated,
        statement_from_date=statement.from_date,
        statement_to_date=statement.to_date,
        new_trade_count=len(trades_df) - len(existing_trades),
        total_trade_count=len(trades_df),
    )


def rebuild_from_raw_statements(config: IbkrFlexApiConfig) -> IbkrSyncResult:
    """Recompute trades.csv / position_snapshots.csv / cash_snapshots.csv
    from every archived raw statement, discarding whatever is currently on
    disk. Use this to recover if the derived CSVs are ever wrong or
    corrupted — `cache_dir/raw_statements/` is the durable record; these
    three files are just a cache of it.

    Does not gap-check: it faithfully reconstructs from whatever was
    archived, which is the same coverage `sync_ibkr_account` already
    verified as gap-free when each statement was originally fetched.
    """
    raw_paths = sorted(_raw_statement_dir(config).glob("*.xml"))
    if not raw_paths:
        raise FileNotFoundError(f"No archived raw statements under {_raw_statement_dir(config)}")

    statements = sorted(
        (_parse_statement(path.read_text(encoding="utf-8")) for path in raw_paths),
        key=lambda statement: statement.when_generated,
    )

    trades_df = pd.DataFrame(columns=_TRADE_COLUMNS)
    positions_df = pd.DataFrame(columns=_POSITION_COLUMNS)
    cash_df = pd.DataFrame(columns=_CASH_COLUMNS)
    for statement in statements:
        trades_df = _merge_trade_history(trades_df, _trades_frame(statement.trades))
        positions_df = _append_snapshot(
            positions_df,
            _snapshot_frame(statement.positions, _POSITION_COLUMNS, statement.when_generated),
        )
        cash_df = _append_snapshot(
            cash_df,
            _snapshot_frame(statement.cash_balances, _CASH_COLUMNS, statement.when_generated),
        )

    _atomic_write_csv(config.cache_dir / "trades.csv", trades_df)
    _atomic_write_csv(config.cache_dir / "position_snapshots.csv", positions_df)
    _atomic_write_csv(config.cache_dir / "cash_snapshots.csv", cash_df)

    return IbkrSyncResult(
        pulled_at=statements[-1].when_generated,
        statement_from_date=min(statement.from_date for statement in statements),
        statement_to_date=max(statement.to_date for statement in statements),
        new_trade_count=len(trades_df),
        total_trade_count=len(trades_df),
    )
