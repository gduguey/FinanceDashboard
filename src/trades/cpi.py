"""The CPI index series, cached to disk as one flat CSV.

Pulled from FRED's public CSV export, which needs no API key. Unlike
`prices.py`, there is no incremental "fetch only the missing range": the
whole series is a small monthly CSV, and FRED revises seasonal
adjustments on already-published months, so an incremental fetch could
miss a revision to old data. `update_cpi_cache` re-fetches and overwrites
the whole series every time; the atomic write still protects against a
crash mid-write.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import polars as pl
import requests

from trades.frames import collect_if_lazy
from trades.io_utils import write_csv_atomic
from trades.models import CpiObservation

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from trades.config import AppConfig

_FRED_MISSING_VALUE = "."


def _cache_path(config: AppConfig) -> Path:
    return config.cpi.cache_dir / f"{config.cpi.series_id}.csv"


def load_cpi_cache(config: AppConfig) -> pl.DataFrame:
    """Read the cached CPI series.

    Parameters
    ----------
    config
        Application configuration; `config.cpi` is read.

    Returns
    -------
    polars.DataFrame
        Columns `observation_date`, `value`, sorted by date. Empty if no cache file exists yet.
    """
    path = _cache_path(config)
    if not path.exists():
        return pl.DataFrame(schema=CpiObservation.polars_schema)
    return pl.read_csv(path, try_parse_dates=True).sort("observation_date")


def fetch_cpi_series(config: AppConfig, session: requests.Session | None = None) -> pl.DataFrame:
    """Pull the full CPI series from FRED's CSV export.

    FRED marks a not-yet-released month's value as `"."`; those rows are
    dropped, not treated as an error. Every remaining row is validated
    through `CpiObservation`.

    Parameters
    ----------
    config
        Application configuration; `config.cpi` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        Columns `observation_date`, `value`.
    """
    http = session or requests
    response = http.get(
        config.cpi.csv_url_template.format(series_id=config.cpi.series_id),
        params={"id": config.cpi.series_id},
        timeout=config.cpi.request_timeout_seconds,
    )
    response.raise_for_status()
    raw = pl.read_csv(io.StringIO(response.text))
    date_column, value_column = raw.columns[0], raw.columns[1]
    raw = raw.filter(pl.col(value_column) != _FRED_MISSING_VALUE)

    observations = [
        CpiObservation(observation_date=row[date_column], value=float(row[value_column]))
        for row in raw.iter_rows(named=True)
    ]
    return pl.DataFrame(
        {
            "observation_date": [observation.observation_date for observation in observations],
            "value": [observation.value for observation in observations],
        },
        schema=CpiObservation.polars_schema,
    )


def update_cpi_cache(config: AppConfig, session: requests.Session | None = None) -> pl.DataFrame:
    """Re-fetch the CPI series and overwrite the on-disk cache with it.

    Parameters
    ----------
    config
        Application configuration; `config.cpi` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        The freshly fetched series.
    """
    series = fetch_cpi_series(config, session)
    write_csv_atomic(series.sort("observation_date"), _cache_path(config))
    return series


def cpi_as_of(history: pl.DataFrame | pl.LazyFrame, target_date: date) -> float | None:
    """Look up the most recent index value on or before a target date.

    Parameters
    ----------
    history
        CPI history with `observation_date` and `value` columns.
    target_date
        The date to look up.

    Returns
    -------
    float or None
        The most recent index value on or before `target_date`, or None if
        the history starts after `target_date`.
    """
    filtered = history.filter(pl.col("observation_date") <= target_date).sort("observation_date").tail(1)
    eligible = collect_if_lazy(filtered)
    if eligible.is_empty():
        return None
    return float(eligible["value"].item())
