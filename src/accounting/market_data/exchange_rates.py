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


def smoothed_rate_series(
    history: pl.DataFrame,
    currencies: Iterable[CurrencyCode] = SUPPORTED_CURRENCIES,
    window_days: int = DEFAULT_SMOOTHING_WINDOW_DAYS,
) -> pl.DataFrame:
    """`smoothed_rate_as_of` evaluated on *every* calendar day the cache spans, in one pass per currency.

    What `current_rates_to_base` is to one date, this is to all of them:
    the same trailing mean, the same window, computed for every day rather
    than one. It exists because an aggregation over dated flows (the income
    statement, budgets, the spend curve, goals) has to convert each row at
    its own date's rate, and calling `smoothed_rate_as_of` once per
    distinct posting date would re-filter the whole history per date.

    Every calendar day gets a row, not only the days the ECB published on
    (it publishes on weekdays; people spend money on Saturdays). The
    trailing mean is over whatever observations actually fall in the
    window, so a Saturday's answer is Saturday's window, not Friday's — the
    same number `smoothed_rate_as_of(history, currency, saturday)` returns,
    which is the property this function is tested against directly.

    Days before a currency's own history starts are filled backward from
    its first computable mean rather than left null. That is the same
    clamp `ledger.currency.with_converted_amount` applies on the far side
    for a posting dated after the cache's last day, and it is a deliberate
    choice over two alternatives: a null rate would silently drop those
    rows from every sum (a left join, a null product, a `sum` that skips
    nulls), and refusing outright would make one 2019 posting a 400 for the
    whole income statement. The cache holds
    `DEFAULT_HISTORY_YEARS` years, so anything older is converted at the
    oldest rate on file, and `docs/accounting/currency-handling.md` says so.

    Parameters
    ----------
    history
        Exchange-rate history, as `load_rate_history` returns it.
    currencies
        Every currency a rate series is needed for; defaults to every supported one.
    window_days
        How many trailing calendar days to average over.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `currency`, `rate_to_base`, one row per (calendar
        day in the cache's span, requested currency). `BASE_CURRENCY` is
        included at `1.0` on every day, so a caller can divide by the
        display currency's own rate without special-casing the base. Empty
        if `history` is, which only happens when nothing but the base
        currency is in play (anything else raises below).

    Raises
    ------
    ValueError
        If a requested non-base currency has no rate history at all — the
        same refusal `current_rates_to_base` makes, for the same reason.
    """
    requested = [code for code in currencies if code != BASE_CURRENCY]
    if history.is_empty():
        if requested:
            message = f"No exchange-rate history for {requested[0]!r} — sync exchange rates first."
            raise ValueError(message)
        return pl.DataFrame(schema=RATE_HISTORY_SCHEMA)

    span = pl.date_range(cast("date", history["date"].min()), cast("date", history["date"].max()), eager=True).alias(
        "date"
    )
    # `window_size` is one day wider than `window_days` because `closed="right"`
    # excludes the window's own left edge: `(t - 31d, t]` is exactly the
    # `date >= t - 30d and date <= t` that `smoothed_rate_as_of` filters on.
    window = pl.col("rate_to_base").rolling_mean_by("date", window_size=f"{window_days + 1}d", closed="right")
    series = [pl.DataFrame({"date": span}).with_columns(currency=pl.lit(BASE_CURRENCY), rate_to_base=pl.lit(1.0))]
    for code in requested:
        observations = history.filter(pl.col("currency") == code).select("date", "rate_to_base")
        if observations.is_empty():
            message = f"No exchange-rate history for {code!r} — sync exchange rates first."
            raise ValueError(message)
        # The calendar days carry a null rate and so contribute nothing to any
        # mean; they are here purely to be the rows the window is evaluated
        # *at*. Filtering back down to them afterwards is what leaves one row
        # per calendar day rather than one per day plus one per observation.
        calendar = pl.DataFrame({"date": span}).with_columns(rate_to_base=pl.lit(None, dtype=pl.Float64))
        rolled = (
            pl
            .concat([observations, calendar])
            .sort("date")
            .with_columns(smoothed=window)
            .filter(pl.col("rate_to_base").is_null())
            .select("date", currency=pl.lit(code), rate_to_base=pl.col("smoothed").fill_null(strategy="backward"))
        )
        series.append(rolled)
    return pl.concat(series).cast(RATE_HISTORY_SCHEMA)  # type: ignore[arg-type]


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
