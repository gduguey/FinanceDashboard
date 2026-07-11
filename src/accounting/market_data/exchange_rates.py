"""Exchange-rate history, cached to disk, and the smoothed rate this app actually converts with.

Pulled from Frankfurter (https://frankfurter.dev), an ECB-sourced, free,
no-API-key service — the same "no credentials needed" shape as
`trades.market_data.cpi`/`hysa_rates`. Every fetch asks for every
non-`BASE_CURRENCY` code in `accounting.models.SUPPORTED_CURRENCIES` at
once; adding a new `CurrencyCode` needs no change here — the next fetch
just picks it up.

A day-to-day spot rate is noisy — pricing net worth off yesterday's tick
would make it swing on pure FX noise having nothing to do with what you
actually own. This uses a trailing 30-day average instead, the rolling
equivalent of the "average rate" method IAS 21 has multinationals use to
translate a period's P&L (there, a calendar-month bucket that resets; here,
a continuously-updated rolling window so it never stair-steps).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import polars as pl
import requests

from accounting.models import BASE_CURRENCY, SUPPORTED_CURRENCIES
from accounting.utils.cache_backup import backup_cache_file, restore_cache_file
from accounting.utils.io_utils import write_csv_atomic

if TYPE_CHECKING:
    from collections.abc import Iterable

    from accounting.config import AccountingConfig
    from accounting.models import CurrencyCode

_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{start}..{end}"
DEFAULT_SMOOTHING_WINDOW_DAYS = 30
DEFAULT_HISTORY_YEARS = 2

RATE_HISTORY_SCHEMA: dict[str, type[pl.DataType] | pl.DataType] = {
    "date": pl.Date,
    "currency": pl.Utf8,
    "rate_to_base": pl.Float64,
}

_CACHE_BACKUP_KEY = "exchange_rates.csv"
"""Key `backup_cache_file`/`restore_cache_file` store the cached history's backup under."""


def _save_raw_response(config: AccountingConfig, response_text: str) -> None:
    """Archive the raw Frankfurter response with a timestamp, never overwritten."""
    raw_dir = config.exchange_rates_raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
    (raw_dir / f"{timestamp}.json").write_text(response_text, encoding="utf-8")


def load_rate_history(config: AccountingConfig) -> pl.DataFrame:
    """Read the cached daily exchange-rate history.

    If the cache file exists but fails to parse (e.g. a botched write
    outside the app's control, a Docker volume issue — file corruption,
    not a failed fetch: a failed fetch never touches this file in the
    first place, see `update_rate_history_cache`), this transparently
    restores the last known-good backup (`restore_cache_file`) and
    retries the read once. A second failure after restoring propagates
    uncaught, and a corrupted file with no backup available re-raises the
    original parse error rather than pretending the cache is empty — a
    human needs to see either case, not silently get wrong data.

    Parameters
    ----------
    config
        Application configuration; `config.exchange_rates_csv_path` is read.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `currency`, `rate_to_base`. Empty if never synced.

    Raises
    ------
    polars.exceptions.ComputeError
        If the cache file is corrupted and either the restored backup is
        itself unreadable, or no backup exists to restore.
    """
    if not config.exchange_rates_csv_path.exists():
        return pl.DataFrame(schema=RATE_HISTORY_SCHEMA)
    try:
        return pl.read_csv(config.exchange_rates_csv_path, schema_overrides=RATE_HISTORY_SCHEMA, try_parse_dates=True)
    except pl.exceptions.ComputeError:
        if not restore_cache_file(config.exchange_rates_csv_path, _CACHE_BACKUP_KEY):
            raise
        return pl.read_csv(config.exchange_rates_csv_path, schema_overrides=RATE_HISTORY_SCHEMA, try_parse_dates=True)


def _fetch_rate_history_range(
    config: AccountingConfig, start: date, end: date, session: requests.Session | None = None
) -> pl.DataFrame:
    """Pull every non-base currency's daily rate for `[start, end]` from Frankfurter.

    The raw response is archived before parsing, per this repo's
    cache-raw-first rule. Split out from `fetch_rate_history` so
    `update_rate_history_cache` can request an explicit incremental range
    instead of always requesting the full `history_years`-year window.

    Parameters
    ----------
    config
        Application configuration; `config.exchange_rates_raw_dir` is written to.
    start
        First date to fetch, inclusive.
    end
        Last date to fetch, inclusive.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `currency`, `rate_to_base` — how many `BASE_CURRENCY`
        units one unit of `currency` was worth on `date`.
    """
    symbols = [code for code in SUPPORTED_CURRENCIES if code != BASE_CURRENCY]

    http = session or requests
    response = http.get(
        _FRANKFURTER_URL.format(start=start.isoformat(), end=end.isoformat()),
        params={"base": BASE_CURRENCY, "symbols": ",".join(symbols)},
        timeout=15,
    )
    response.raise_for_status()
    _save_raw_response(config, response.text)

    payload = json.loads(response.text)
    rows = [
        {"date": date.fromisoformat(day), "currency": currency, "rate_to_base": 1.0 / value}
        for day, currencies in payload["rates"].items()
        for currency, value in currencies.items()
    ]
    return pl.DataFrame(rows, schema=RATE_HISTORY_SCHEMA)


def fetch_rate_history(
    config: AccountingConfig, history_years: int = DEFAULT_HISTORY_YEARS, session: requests.Session | None = None
) -> pl.DataFrame:
    """Pull every non-base currency's daily rate for the last `history_years` years from Frankfurter.

    Parameters
    ----------
    config
        Application configuration; `config.exchange_rates_raw_dir` is written to.
    history_years
        How many years of daily history to request.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `currency`, `rate_to_base` — how many `BASE_CURRENCY`
        units one unit of `currency` was worth on `date`.
    """
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=history_years * 365)
    return _fetch_rate_history_range(config, start, end, session)


def update_rate_history_cache(
    config: AccountingConfig, history_years: int = DEFAULT_HISTORY_YEARS, session: requests.Session | None = None
) -> pl.DataFrame:
    """Incrementally refresh the cached exchange-rate history, fetching only what's missing.

    Checks the existing cache's latest date (`load_rate_history`) and
    requests Frankfurter only for the range from the day after that
    through today — a run that finds the cache already current makes no
    network call at all. Falls back to the full `history_years`-year
    window only when the cache is empty (never synced), the same
    gap-then-merge-then-write shape as
    `trades.market_data.prices.update_price_cache`, minus the
    per-symbol dimension exchange rates don't have.

    Once the merged history is written, its bytes are backed up
    (`backup_cache_file`) as the new "last known good" copy —
    `load_rate_history` restores from this backup if the cache file is
    ever found corrupted on disk.

    Parameters
    ----------
    config
        Application configuration; `config.exchange_rates_csv_path` is
        both read (for the existing cache) and written.
    history_years
        How many years of daily history to backfill when the cache is empty.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        The full cached history after the update.
    """
    existing = load_rate_history(config)

    if existing.is_empty():
        fetched = fetch_rate_history(config, history_years, session)
    else:
        start = cast("date", existing["date"].max()) + timedelta(days=1)
        end = datetime.now(tz=UTC).date()
        if start > end:
            return existing
        fetched = _fetch_rate_history_range(config, start, end, session)

    if fetched.is_empty():
        return existing

    merged = pl.concat([existing, fetched]).unique(subset=["date", "currency"], keep="last").sort("date", "currency")
    write_csv_atomic(merged, config.exchange_rates_csv_path)
    backup_cache_file(config.exchange_rates_csv_path, _CACHE_BACKUP_KEY)
    return merged


def smoothed_rate_as_of(
    history: pl.DataFrame, currency: CurrencyCode, as_of: date, window_days: int = DEFAULT_SMOOTHING_WINDOW_DAYS
) -> float | None:
    """Average `currency`'s daily rate over the trailing `window_days` ending on `as_of`.

    Parameters
    ----------
    history
        Exchange-rate history, as `load_rate_history` returns it.
    currency
        The currency to look up — never `accounting.models.BASE_CURRENCY`, which is always `1.0`.
    as_of
        The last day of the trailing window, inclusive.
    window_days
        How many trailing calendar days to average over.

    Returns
    -------
    float or None
        The averaged rate, or `None` if no history falls in the window.
    """
    window_start = as_of - timedelta(days=window_days)
    window = history.filter(
        (pl.col("currency") == currency) & (pl.col("date") >= window_start) & (pl.col("date") <= as_of)
    )
    if window.is_empty():
        return None
    return float(window["rate_to_base"].mean())  # type: ignore[arg-type]


def current_rates_to_base(
    history: pl.DataFrame,
    as_of: date,
    currencies: Iterable[CurrencyCode] = SUPPORTED_CURRENCIES,
    window_days: int = DEFAULT_SMOOTHING_WINDOW_DAYS,
) -> dict[CurrencyCode, float]:
    """Build the rate table one conversion needs: each requested currency's smoothed rate into the base.

    Only ever requires history for the currencies actually asked for — a
    store with no EUR accounts yet shouldn't be blocked from computing a
    USD-only net worth just because EUR was never synced.

    Parameters
    ----------
    history
        Exchange-rate history, as `load_rate_history` returns it.
    as_of
        The date to compute the smoothed rate as of.
    currencies
        Every currency a rate is needed for; defaults to every supported one.
    window_days
        How many trailing calendar days to average over.

    Returns
    -------
    dict[CurrencyCode, float]
        Each requested code mapped to its rate into `BASE_CURRENCY`
        (`BASE_CURRENCY` itself always `1.0`).

    Raises
    ------
    ValueError
        If a requested non-base currency has no rate history in the
        window — sync exchange rates first, rather than silently defaulting.
    """
    rates: dict[CurrencyCode, float] = {BASE_CURRENCY: 1.0}
    for code in currencies:
        if code == BASE_CURRENCY:
            continue
        rate = smoothed_rate_as_of(history, code, as_of, window_days)
        if rate is None:
            message = f"No exchange-rate history for {code!r} as of {as_of} — sync exchange rates first."
            raise ValueError(message)
        rates[code] = rate
    return rates
