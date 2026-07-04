"""Portfolio valuation helpers wired to the on-disk price cache."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import polars as pl

from trades.ledger.replay import portfolio_value, replay_ledger
from trades.market_data import prices

if TYPE_CHECKING:
    from collections.abc import Callable

    from trades.config import AppConfig


def make_price_lookup(config: AppConfig, *, adjusted: bool = False) -> Callable[[str, date], float | None]:
    """Build a `price_lookup` callable backed by the on-disk price cache.

    Every `ledger.*` function that needs a price takes a plain callable
    rather than a config, so it stays agnostic of where prices come from.
    This is the one place that wires that callable to `market_data.prices`,
    reading each symbol's cache file at most once per call regardless of
    how many dates it's asked to price.

    Parameters
    ----------
    config
        Application configuration; `config.prices.cache_dir` is read.
    adjusted
        Look up the dividend/split-adjusted series instead of the raw
        close (see `market_data.prices`'s module docstring) — the raw
        series for pricing your own positions, the adjusted series for
        benchmark counterfactuals.

    Returns
    -------
    Callable[[str, datetime.date], float or None]
        Looks up a symbol's price as of a given date; returns None if
        the symbol has never been cached.
    """
    histories: dict[str, pl.DataFrame] = {}

    def lookup(symbol: str, as_of: date) -> float | None:
        if symbol not in histories:
            histories[symbol] = prices.load_price_cache(symbol, config, adjusted=adjusted)
        return prices.price_as_of(histories[symbol], as_of)

    return lookup


def daily_portfolio_values(
    ledger: pl.DataFrame,
    price_lookup: Callable[[str, date], float | None],
    start: date,
    end: date,
    config: AppConfig,
) -> pl.DataFrame:
    """Compute portfolio value for every calendar day in a range.

    The backbone series for the dollar chart, the Net Asset Value (NAV)
    series, growth-of-100, monthly P&L, and max drawdown — all derived
    from the same day-by-day valuation rather than each recomputing it.
    Replays the ledger truncated to each day (see
    `counterfactuals.decision_counterfactual_value`'s docstring for why
    truncation, not just `replay_ledger`, is required for an as-of value)
    and prices the result; a day with no ledger activity yet has zero
    value rather than being omitted, so the series has no gaps.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    start
        First day to value, inclusive.
    end
        Last day to value, inclusive.
    config
        Application configuration, passed through to `replay_ledger`.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value`, one row per calendar day in `[start, end]`.
    """
    dates = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    values = [
        portfolio_value(
            replay_ledger(ledger.filter(pl.col("event_datetime").dt.date() <= day), config),
            price_lookup,
            day,
        )
        for day in dates
    ]
    return pl.DataFrame({"date": dates, "value": values})
