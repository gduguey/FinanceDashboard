"""Daily close prices from Yahoo Finance's public chart endpoint.

Cached to disk as one flat CSV per symbol. Two series are cached per
symbol: the raw daily close (for pricing your own positions) and the
dividend/split-adjusted close (for benchmark counterfactuals, e.g. an
all-VOO comparison) — mixing the two silently corrupts every return, so
they are separate cache files, selected by the `adjusted` flag every
function here takes. Both come from the same Yahoo chart API response,
just a different field.

The raw close is cached incrementally (`update_price_cache` only fetches
what's genuinely missing) — except for the trailing `_SETTLEMENT_BUFFER_DAYS`
days, which are always re-fetched on every call regardless of what's
already cached. A fetch made while the market is still open can get a
live, still-moving close for the current day back from Yahoo; without the
buffer, that value would be cached as final and never revisited once the
day's real close is known. The adjusted close is never cached
incrementally at all (`refresh_adjusted_price_history`) — Yahoo
retroactively recalculates `adjclose` for a symbol's entire history every
time it pays a new dividend or splits, so even a date fetched and cached
long ago can silently go stale.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import polars as pl
import requests

from trades.models import PriceObservation
from trades.utils.cache_backup import backup_cache_file
from trades.utils.frames import collect_if_lazy
from trades.utils.io_utils import read_csv_recovering_from_corruption, write_csv_atomic

if TYPE_CHECKING:
    from pathlib import Path

    from trades.config import AppConfig

_SETTLEMENT_BUFFER_DAYS = 2
"""Trailing days always re-fetched and overwritten by `update_price_cache`, even if already cached.

A fetch made before the market closes for the day can get a live,
still-moving close back from Yahoo for that day; the next call within this
many days re-requests it so the eventual settled close overwrites it. Kept
well short of a week so ordinary incremental efficiency is barely affected.
"""


def _cache_path(symbol: str, cache_dir: Path, *, adjusted: bool) -> Path:
    suffix = ".adjusted" if adjusted else ""
    return cache_dir / f"{symbol.upper()}{suffix}.csv"


def _backup_key(symbol: str, *, adjusted: bool) -> str:
    suffix = ".adjusted" if adjusted else ""
    return f"prices/{symbol.upper()}{suffix}.csv"


def _to_unix_seconds(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=UTC).timestamp())


def load_price_cache(symbol: str, config: AppConfig, *, adjusted: bool = False) -> pl.DataFrame:
    """Read a symbol's cached price history.

    Parameters
    ----------
    symbol
        The ticker symbol.
    config
        Application configuration; `config.prices.cache_dir` is read.
    adjusted
        Read the dividend/split-adjusted cache instead of the raw-close cache.

    Returns
    -------
    polars.DataFrame
        Columns `price_date`, `close`, sorted by date. Empty if no cache file exists yet.
    """
    path = _cache_path(symbol, config.prices.cache_dir, adjusted=adjusted)
    if not path.exists():
        return pl.DataFrame(schema=PriceObservation.polars_schema)
    return read_csv_recovering_from_corruption(path, _backup_key(symbol, adjusted=adjusted)).sort("price_date")


def fetch_price_history(
    symbol: str,
    start: date,
    end: date,
    config: AppConfig,
    *,
    adjusted: bool = False,
    session: requests.Session | None = None,
) -> pl.DataFrame:
    """Pull daily closes for a symbol from the Yahoo chart API.

    Parameters
    ----------
    symbol
        The ticker symbol to fetch.
    start
        First date to fetch, inclusive.
    end
        Last date to fetch, inclusive.
    config
        Application configuration; `config.prices` is read.
    adjusted
        Fetch the dividend/split-adjusted close instead of the raw close.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        Columns `price_date`, `close`. Missing/holiday days are absent, not filled in.

    Raises
    ------
    ValueError
        If the API responds with no data at all.
    """
    http = session or requests
    params: dict[str, int | str] = {
        "period1": _to_unix_seconds(start),
        "period2": _to_unix_seconds(end + timedelta(days=1)),
        "interval": "1d",
    }
    response = http.get(
        config.prices.chart_url_template.format(symbol=symbol),
        params=params,
        headers=config.prices.request_headers,
        timeout=config.prices.request_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        message = f"Yahoo chart API returned no data for {symbol}: {error}"
        raise ValueError(message)

    timestamps = result[0]["timestamp"]
    indicators = result[0]["indicators"]
    values = indicators["adjclose"][0]["adjclose"] if adjusted else indicators["quote"][0]["close"]
    observations = [
        PriceObservation(symbol=symbol, price_date=datetime.fromtimestamp(ts, tz=UTC).date(), close=value)
        for ts, value in zip(timestamps, values, strict=True)
        if value is not None
    ]
    return pl.DataFrame(
        {
            "price_date": [observation.price_date for observation in observations],
            "close": [observation.close for observation in observations],
        },
        schema=PriceObservation.polars_schema,
    )


def _missing_ranges(existing: pl.DataFrame, since: date, as_of: date) -> list[tuple[date, date]]:
    """Compute the date range(s) not yet covered by an existing cache.

    Parameters
    ----------
    existing
        The currently cached history.
    since
        Start of the window the caller wants covered.
    as_of
        End of the window the caller wants covered.

    Returns
    -------
    list[tuple[datetime.date, datetime.date]]
        Zero, one, or two (start, end) gaps at the front and/or back of `existing`.
    """
    if existing.is_empty():
        return [(since, as_of)]
    existing_min = cast("date", existing["price_date"].min())
    existing_max = cast("date", existing["price_date"].max())
    gaps: list[tuple[date, date]] = []
    if since < existing_min:
        gaps.append((since, existing_min - timedelta(days=1)))
    if as_of > existing_max:
        gaps.append((existing_max + timedelta(days=1), as_of))
    return gaps


def update_price_cache(
    symbol: str,
    since: date,
    as_of: date,
    config: AppConfig,
    *,
    adjusted: bool = False,
    session: requests.Session | None = None,
) -> pl.DataFrame:
    """Ensure the on-disk cache for a symbol covers [since, as_of], fetching what's missing or unsettled.

    "Missing" is computed against the cache with its trailing
    `_SETTLEMENT_BUFFER_DAYS` days hidden — those days always look
    missing and get re-requested, even if already cached, since a close
    fetched before market close can be live and still-moving (see the
    module docstring). The full, unfiltered cache is still merged back in
    afterwards, so if the fresh fetch legitimately has nothing for a date
    in that buffer window (a weekend, a holiday, a transient gap), the old
    cached value for that date is kept as a fallback rather than lost.

    Parameters
    ----------
    symbol
        The ticker symbol.
    since
        Start of the window the cache must cover.
    as_of
        End of the window the cache must cover.
    config
        Application configuration; `config.prices` is read.
    adjusted
        Update the dividend/split-adjusted cache instead of the raw-close cache.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        The full cached history after the update.
    """
    existing = load_price_cache(symbol, config, adjusted=adjusted)
    settlement_cutoff = as_of - timedelta(days=_SETTLEMENT_BUFFER_DAYS)
    settled = existing.filter(pl.col("price_date") <= settlement_cutoff)
    gaps = _missing_ranges(settled, since, as_of)
    if not gaps:
        return existing

    fetched = [
        fetch_price_history(symbol, gap_start, gap_end, config, adjusted=adjusted, session=session)
        for gap_start, gap_end in gaps
    ]
    if all(frame.is_empty() for frame in fetched):
        return existing

    merged = pl.concat([existing, *fetched]).unique(subset="price_date", keep="last").sort("price_date")
    path = _cache_path(symbol, config.prices.cache_dir, adjusted=adjusted)
    write_csv_atomic(merged, path)
    backup_cache_file(path, _backup_key(symbol, adjusted=adjusted))
    return merged


def update_price_caches(
    symbols: list[str],
    since: date,
    as_of: date,
    config: AppConfig,
    *,
    adjusted: bool = False,
    session: requests.Session | None = None,
) -> dict[str, pl.DataFrame]:
    """Ensure the on-disk cache for every symbol covers [since, as_of].

    Parameters
    ----------
    symbols
        The ticker symbols to update.
    since
        Start of the window the cache must cover.
    as_of
        End of the window the cache must cover.
    config
        Application configuration; `config.prices` is read.
    adjusted
        Update the dividend/split-adjusted cache instead of the raw-close cache.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    dict[str, polars.DataFrame]
        Each symbol's full cached history after the update.
    """
    return {
        symbol: update_price_cache(symbol, since, as_of, config, adjusted=adjusted, session=session)
        for symbol in symbols
    }


def refresh_adjusted_price_history(
    symbol: str,
    since: date,
    as_of: date,
    config: AppConfig,
    *,
    session: requests.Session | None = None,
) -> pl.DataFrame:
    """Fully re-fetch a symbol's adjusted-close series and overwrite its cache with it.

    Deliberately not incremental, unlike `update_price_cache`: Yahoo
    retroactively recalculates `adjclose` for every historical date
    whenever `symbol` pays a new dividend or splits, so an incremental
    cache that only fetches new dates would let its already-cached dates
    silently drift out of date forever. This re-fetches and overwrites the
    whole [since, as_of] window every time — the same "always full
    rewrite" shape `cpi.update_cpi_cache` uses, for the same kind of
    reason (see that module's docstring).

    Parameters
    ----------
    symbol
        The ticker symbol.
    since
        Start of the window to fetch, inclusive.
    as_of
        End of the window to fetch, inclusive.
    config
        Application configuration; `config.prices` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        The freshly fetched adjusted-close series.
    """
    series = fetch_price_history(symbol, since, as_of, config, adjusted=True, session=session)
    path = _cache_path(symbol, config.prices.cache_dir, adjusted=True)
    write_csv_atomic(series.sort("price_date"), path)
    backup_cache_file(path, _backup_key(symbol, adjusted=True))
    return series


def refresh_adjusted_price_histories(
    symbols: list[str],
    since: date,
    as_of: date,
    config: AppConfig,
    *,
    session: requests.Session | None = None,
) -> dict[str, pl.DataFrame]:
    """Fully re-fetch and overwrite the adjusted-close cache for every symbol in `symbols`.

    Parameters
    ----------
    symbols
        The ticker symbols to refresh.
    since
        Start of the window to fetch, inclusive.
    as_of
        End of the window to fetch, inclusive.
    config
        Application configuration; `config.prices` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    dict[str, polars.DataFrame]
        Each symbol's freshly fetched adjusted-close series.
    """
    return {symbol: refresh_adjusted_price_history(symbol, since, as_of, config, session=session) for symbol in symbols}


def price_as_of(history: pl.DataFrame | pl.LazyFrame, target_date: date) -> float | None:
    """Look up the most recent close on or before a target date.

    Rolls back over weekends/holidays; never interpolates.

    Parameters
    ----------
    history
        Price history with `price_date` and `close` columns.
    target_date
        The date to price as of.

    Returns
    -------
    float or None
        The most recent close on or before `target_date`, or None if the
        history starts after `target_date`.
    """
    eligible = collect_if_lazy(history.filter(pl.col("price_date") <= target_date).sort("price_date").tail(1))
    if eligible.is_empty():
        return None
    return float(eligible["close"].item())
