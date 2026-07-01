"""Daily close prices from Yahoo Finance's public chart endpoint, cached to
disk as one flat CSV per symbol.

Design (see docs/architecture.md and docs/prices_api.md for the full rationale):
  - The cache is append-only from the caller's point of view: `update_price_cache`
    never rewrites a value that's already stored, it only fetches the date
    ranges missing at the front (older than the cache) or back (newer than the
    cache) and appends them.
  - Every row pulled from the API is validated through `PriceObservation`
    before it can reach the cache, so a malformed API response can't corrupt
    stored data.
  - Writes are atomic (write to a temp file, then `Path.replace`), so a crash
    mid-write can't leave a half-written cache file.
  - Reads and re-fetches are bounded to exactly the missing range, so calling
    this once a day only ever costs one small HTTP request per symbol.

Every function below takes a `PriceApiConfig` explicitly — there is no
module-level default cache directory, URL, or timeout to fall back to.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from trades.config import PriceApiConfig
from trades.models import PriceObservation

CACHE_COLUMNS = ["price_date", "close"]  # on-disk schema, not a tunable parameter


def _cache_path(symbol: str, cache_dir: Path) -> Path:
    return cache_dir / f"{symbol.upper()}.csv"


def load_price_cache(symbol: str, config: PriceApiConfig) -> pd.DataFrame:
    """Read a symbol's cached price history, or an empty frame if none exists yet."""
    path = _cache_path(symbol, config.cache_dir)
    if not path.exists():
        return pd.DataFrame(columns=CACHE_COLUMNS).astype({"close": "float64"})
    df = pd.read_csv(path, parse_dates=["price_date"])
    return df.sort_values("price_date").reset_index(drop=True)


def _write_cache_atomic(symbol: str, df: pd.DataFrame, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol, cache_dir)
    tmp_path = path.with_suffix(".csv.tmp")
    df.sort_values("price_date").to_csv(tmp_path, index=False)
    tmp_path.replace(path)  # atomic rename on the same filesystem


def _to_unix(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp())


def fetch_price_history(
    symbol: str,
    start: date,
    end: date,
    config: PriceApiConfig,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Pull daily closes for `symbol` in [start, end] from the Yahoo chart API.

    Every row is validated through `PriceObservation` (positive close, real
    date) before being returned; missing/holiday days are simply absent, not
    filled in. Raises if the API responds with an error or no data at all —
    callers must not receive an empty result mistaken for "no price change."
    """
    http = session or requests
    response = http.get(
        config.chart_url_template.format(symbol=symbol),
        params={
            "period1": _to_unix(start),
            "period2": _to_unix(end + timedelta(days=1)),
            "interval": "1d",
        },
        headers=config.request_headers,
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        raise ValueError(f"Yahoo chart API returned no data for {symbol}: {error}")

    timestamps = result[0]["timestamp"]
    closes = result[0]["indicators"]["quote"][0]["close"]
    observations = [
        PriceObservation(
            symbol=symbol,
            price_date=datetime.fromtimestamp(ts, tz=UTC).date(),
            close=close,
        )
        for ts, close in zip(timestamps, closes, strict=True)
        if close is not None
    ]
    return pd.DataFrame(
        [{"price_date": o.price_date, "close": o.close} for o in observations]
    ).astype({"price_date": "datetime64[ns]"})


def _missing_ranges(existing: pd.DataFrame, since: date, as_of: date) -> list[tuple[date, date]]:
    if existing.empty:
        return [(since, as_of)]
    existing_min = existing["price_date"].min().date()
    existing_max = existing["price_date"].max().date()
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
    config: PriceApiConfig,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Ensure the on-disk cache for `symbol` covers [since, as_of], fetching
    only what's missing, and return the full cached history."""
    existing = load_price_cache(symbol, config)
    gaps = _missing_ranges(existing, since, as_of)
    if not gaps:
        return existing

    fetched = [
        fetch_price_history(symbol, gap_start, gap_end, config, session)
        for gap_start, gap_end in gaps
    ]
    new_rows = (
        pd.concat(fetched, ignore_index=True) if fetched else pd.DataFrame(columns=CACHE_COLUMNS)
    )
    merged = (
        pd.concat([existing, new_rows], ignore_index=True)
        .drop_duplicates(subset="price_date", keep="last")
        .sort_values("price_date")
        .reset_index(drop=True)
    )
    if not new_rows.empty:
        _write_cache_atomic(symbol, merged, config.cache_dir)
    return merged


def update_price_caches(
    symbols: list[str],
    since: date,
    as_of: date,
    config: PriceApiConfig,
    session: requests.Session | None = None,
) -> dict[str, pd.DataFrame]:
    return {s: update_price_cache(s, since, as_of, config, session) for s in symbols}


def price_as_of(history: pd.DataFrame, target_date: date) -> float | None:
    """Most recent close on or before `target_date` (rolls back over
    weekends/holidays); None if the history starts after `target_date`."""
    eligible = history[history["price_date"] <= pd.Timestamp(target_date)]
    if eligible.empty:
        return None
    return float(eligible.sort_values("price_date").iloc[-1]["close"])
