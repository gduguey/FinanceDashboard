"""Portfolio valuation helpers wired to the on-disk price cache."""

from __future__ import annotations

import bisect
from datetime import date, timedelta
from typing import TYPE_CHECKING

import polars as pl

from trades.ledger.replay import portfolio_value, replay_ledger
from trades.market_data import prices
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable

    from trades.config import AppConfig
    from trades.ledger.replay import ReplayResult


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
        """Look up `symbol`'s price as of `as_of`, caching its loaded history across calls.

        Returns
        -------
        float or None
        """
        if symbol not in histories:
            histories[symbol] = prices.load_price_cache(symbol, config, adjusted=adjusted)
        return prices.price_as_of(histories[symbol], as_of)

    return lookup


def daily_portfolio_values(
    ledger: pl.DataFrame | pl.LazyFrame,
    price_lookup: Callable[[str, date], float | None],
    start: date,
    end: date,
    config: AppConfig,
) -> pl.DataFrame | pl.LazyFrame:
    """Compute portfolio value for every calendar day in a range.

    The backbone series for the dollar chart, the Net Asset Value (NAV)
    series, growth-of-100, monthly P&L, and max drawdown — all derived
    from the same day-by-day valuation rather than each recomputing it.

    Optimization: Instead of replaying the ledger for every calendar day
    (O(days x ledger size)), this replays only at event dates and
    forward-fills the resulting *position* for days with no events —
    positions can't change without an event. Each calendar day is still
    priced individually via `price_lookup` using its own date, since a
    price (unlike a position) keeps moving on days with no ledger
    activity; `price_lookup` is a cheap in-memory cache lookup (see
    `make_price_lookup`), so this daily repricing is negligible next to
    the replay itself. Net effect: replay calls drop to
    O(unique_event_dates), while the value stays accurate for every day.

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
    polars.DataFrame or polars.LazyFrame
        Columns `date`, `value`, one row per calendar day in `[start, end]`.
        Same type as input.
    """
    was_eager = isinstance(ledger, pl.DataFrame)
    ledger_df = collect_if_lazy(ledger)

    # Extract unique event dates in [start, end] range using Polars
    event_dates_result = ledger_df.filter(
        (pl.col("event_datetime").dt.date() >= start) & (pl.col("event_datetime").dt.date() <= end)
    ).select(pl.col("event_datetime").dt.date().unique().sort())
    event_dates: list[date] = [] if event_dates_result.is_empty() else event_dates_result["event_datetime"].to_list()

    # Compute replayed position at each event date — positions are what's
    # actually cheap to forward-fill, since they don't move without an event.
    state_by_date: dict[date, ReplayResult] = {}
    for event_date in event_dates:
        state_by_date[event_date] = replay_ledger(
            ledger_df.filter(pl.col("event_datetime").dt.date() <= event_date), config
        )

    # Generate all calendar dates: forward-fill the position, but reprice
    # it as of that calendar day's own date, not the event date.
    all_dates = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    values: list[float] = []
    for cal_date in all_dates:
        # Find most recent event date <= this calendar date (event_dates is sorted)
        idx = bisect.bisect_right(event_dates, cal_date) - 1
        if idx >= 0:
            state = state_by_date[event_dates[idx]]
            values.append(portfolio_value(state, price_lookup, cal_date))
        else:
            # No events yet; portfolio value is 0
            values.append(0.0)

    result = pl.DataFrame({"date": all_dates, "value": values})
    return result if was_eager else result.lazy()
