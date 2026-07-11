"""Historical HYSA bank APY rates, scraped from apyarchives.com and cached to disk.

apyarchives.com is a Next.js app; the full rate history for every bank is
embedded directly in the server-rendered HTML as a React Server
Components payload (`self.__next_f.push([1, "..."])` chunks), not fetched
by a separate API call afterward — so no JS execution is needed, just
finding and parsing that embedded JSON. Cached the same way as `cpi.py`:
one small file, re-fetched and overwritten in full on every sync (a
bank's published history could be corrected upstream after the fact).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import polars as pl
import requests

from trades.models import HysaRateObservation
from trades.utils.cache_backup import backup_cache_file
from trades.utils.frames import collect_if_lazy
from trades.utils.io_utils import read_csv_recovering_from_corruption, write_csv_atomic

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from trades.config import AppConfig

_NEXT_F_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_ACCOUNTS_KEY = '"accounts":['
_BACKUP_KEY = "hysa_rates.csv"


def _cache_path(config: AppConfig) -> Path:
    return config.hysa_rates.cache_dir / "rates.csv"


def load_hysa_rates_cache(config: AppConfig) -> pl.DataFrame:
    """Read the cached HYSA bank-rate history.

    Parameters
    ----------
    config
        Application configuration; `config.hysa_rates.cache_dir` is read.

    Returns
    -------
    polars.DataFrame
        Columns `bank_id`, `bank_name`, `rate_date`, `apy_pct`, sorted by
        bank then date. Empty if no cache file exists yet.
    """
    path = _cache_path(config)
    if not path.exists():
        return pl.DataFrame(schema=HysaRateObservation.polars_schema)
    return read_csv_recovering_from_corruption(path, _BACKUP_KEY).sort("bank_id", "rate_date")


def _extract_accounts_json(html: str) -> str:
    r"""Find the raw `"accounts": [...]` JSON array embedded in the page.

    Every RSC chunk is itself a JSON-escaped string (`json.loads` on the
    quoted chunk correctly unescapes it, including any `\"` inside it);
    once unescaped, the `accounts` array is real, parseable JSON even
    though the chunk as a whole is React's internal wire format, not JSON.

    Returns
    -------
    str
        The `[...]` JSON array text.

    Raises
    ------
    ValueError
        If no embedded chunk contains an `accounts` array — the page's
        structure has likely changed upstream.
    """
    for match in _NEXT_F_CHUNK.finditer(html):
        chunk = json.loads(match.group(1))
        start = chunk.find(_ACCOUNTS_KEY)
        if start == -1:
            continue
        array_start = start + len(_ACCOUNTS_KEY) - 1
        depth = 0
        for i in range(array_start, len(chunk)):
            if chunk[i] == "[":
                depth += 1
            elif chunk[i] == "]":
                depth -= 1
                if depth == 0:
                    return chunk[array_start : i + 1]
    message = "No 'accounts' data found on apyarchives.com — its page structure may have changed."
    raise ValueError(message)


def fetch_hysa_rates(config: AppConfig, session: requests.Session | None = None) -> pl.DataFrame:
    """Scrape every bank's full APY rate history from apyarchives.com.

    Parameters
    ----------
    config
        Application configuration; `config.hysa_rates` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        Columns `bank_id`, `bank_name`, `rate_date`, `apy_pct` — one row
        per rate change per bank (not one row per day).
    """
    http = session or requests
    response = http.get(
        config.hysa_rates.source_url,
        headers=config.hysa_rates.request_headers,
        timeout=config.hysa_rates.request_timeout_seconds,
    )
    response.raise_for_status()
    accounts = json.loads(_extract_accounts_json(response.text))

    observations = [
        HysaRateObservation(
            bank_id=account["id"],
            bank_name=account["name"],
            rate_date=point["date"],
            apy_pct=point["apy"],
        )
        for account in accounts
        for point in account["history"]
    ]
    return pl.DataFrame(
        {
            "bank_id": [observation.bank_id for observation in observations],
            "bank_name": [observation.bank_name for observation in observations],
            "rate_date": [observation.rate_date for observation in observations],
            "apy_pct": [observation.apy_pct for observation in observations],
        },
        schema=HysaRateObservation.polars_schema,
    )


def update_hysa_rates_cache(config: AppConfig, session: requests.Session | None = None) -> pl.DataFrame:
    """Re-scrape apyarchives.com and overwrite the on-disk cache with it.

    Parameters
    ----------
    config
        Application configuration; `config.hysa_rates` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    polars.DataFrame
        The freshly scraped rate history.
    """
    series = fetch_hysa_rates(config, session)
    path = _cache_path(config)
    write_csv_atomic(series.sort("bank_id", "rate_date"), path)
    backup_cache_file(path, _BACKUP_KEY)
    return series


def list_banks(history: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """List every bank present in a rate history, for a bank picker.

    Parameters
    ----------
    history
        A rate history, as returned by `load_hysa_rates_cache`.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `bank_id`, `bank_name`, one row per bank, sorted by name.
        Same type as input.
    """
    was_eager = isinstance(history, pl.DataFrame)
    result = history.select("bank_id", "bank_name").unique().sort("bank_name")
    if not was_eager:
        return result
    return collect_if_lazy(result)


def rate_as_of(history: pl.DataFrame | pl.LazyFrame, bank_id: str, target_date: date) -> float | None:
    """Look up a bank's most recently published APY on or before a date.

    Rates only get a new row on the date they changed, so this rolls back
    to the latest change at or before `target_date` — the same convention
    as `prices.price_as_of`/`cpi.cpi_as_of`.

    Parameters
    ----------
    history
        A rate history, as returned by `load_hysa_rates_cache`.
    bank_id
        The bank to look up.
    target_date
        The date to look up the rate as of.

    Returns
    -------
    float or None
        The APY (as a percentage, e.g. `4.25`) in effect on `target_date`,
        or None if `bank_id` is unknown or its history starts after `target_date`.
    """
    filtered = (
        history.filter((pl.col("bank_id") == bank_id) & (pl.col("rate_date") <= target_date)).sort("rate_date").tail(1)
    )
    eligible = collect_if_lazy(filtered)
    if eligible.is_empty():
        return None
    return float(eligible["apy_pct"].item())
