"""Per-trade return math.

Total return, CAGR-style annualization, a compounded HYSA benchmark over
the same window, and the resulting alpha. See docs/returns.md for the
derivation of each step. Every function that needs the annualization
convention or the benchmark rate takes `AppConfig` explicitly — there is
no module-level default to fall back to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from trades.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig


def total_return_pct(price_paid: float, current_price: float) -> float:
    """Compute the simple percentage return on a position.

    Parameters
    ----------
    price_paid
        The per-share price paid.
    current_price
        The per-share price to compare against.

    Returns
    -------
    float
        The percentage gain (or loss) from `price_paid` to `current_price`.
    """
    return (current_price - price_paid) / price_paid * 100


def annualized_return_pct(total_return_pct_value: float, days_held: int, config: AppConfig) -> float:
    """Compound an observed return up to a full year's rate.

    Very short holds produce large-looking numbers by design — that noise
    is why the combined portfolio alpha uses period alpha instead, see
    `portfolio_alpha_pct`.

    Parameters
    ----------
    total_return_pct_value
        The observed percentage return over `days_held` days.
    days_held
        The number of days the position was held.
    config
        Application configuration; `config.returns.annualization_days` is read.

    Returns
    -------
    float
        The annualized percentage return, or NaN if `days_held` is 0
        (nothing to annualize).

    Raises
    ------
    ValueError
        If `days_held` is negative.
    """
    if days_held < 0:
        message = f"days_held must be >= 0, got {days_held} (as_of before the trade date?)"
        raise ValueError(message)
    if days_held == 0:
        return float("nan")
    growth = 1 + total_return_pct_value / 100
    return (growth ** (config.returns.annualization_days / days_held) - 1) * 100


def hysa_period_return_pct(days_held: int, config: AppConfig) -> float:
    """Compute what a compounding HYSA would return over a holding period.

    Parameters
    ----------
    days_held
        The number of days to compound over.
    config
        Application configuration; `config.returns.hysa_annual_rate` and
        `config.returns.annualization_days` are read.

    Returns
    -------
    float
        The percentage return a HYSA at `config.returns.hysa_annual_rate`
        would produce over `days_held` days.

    Raises
    ------
    ValueError
        If `days_held` is negative.
    """
    if days_held < 0:
        message = f"days_held must be >= 0, got {days_held} (as_of before the trade date?)"
        raise ValueError(message)
    return ((1 + config.returns.hysa_annual_rate) ** (days_held / config.returns.annualization_days) - 1) * 100


def build_returns_table(
    trades: pl.DataFrame | pl.LazyFrame,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: AppConfig,
) -> pl.DataFrame | pl.LazyFrame:
    """Build one row per trade: current price, days held, and every return figure.

    `price_lookup` is an arbitrary Python callback (a cache lookup), not a
    polars expression, so each row is priced by a plain `for` loop rather
    than a vectorized computation.

    Parameters
    ----------
    trades
        One row per trade, with `symbol`, `trade_date`, `usd_per_share` columns.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The date to price every trade as of.
    config
        Application configuration; `config.returns` is read.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `trades` plus `as_of_date`, `current_price`, `days_held`,
        `total_return_pct`, `annualized_return_pct`,
        `hysa_period_return_pct`, `alpha_period_pct`. Same type as `trades`.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any trade — a missing price
        means the cache hasn't been updated for that symbol/date, and
        silently dropping the trade would produce an incomplete table
        with no indication anything was skipped.
    """
    was_eager = isinstance(trades, pl.DataFrame)
    records = collect_if_lazy(trades).to_dicts()

    rows = []
    for record in records:
        symbol = record["symbol"]
        current_price = price_lookup(symbol, as_of)
        if current_price is None:
            message = (
                f"No price available for {symbol} on or before {as_of}. Update the price "
                "cache (prices.update_price_cache) before building the returns table."
            )
            raise ValueError(message)
        days_held = (as_of - record["trade_date"]).days
        total_ret = total_return_pct(record["usd_per_share"], current_price)
        hysa_ret = hysa_period_return_pct(days_held, config)
        rows.append({
            **record,
            "as_of_date": as_of,
            "current_price": current_price,
            "days_held": days_held,
            "total_return_pct": total_ret,
            "annualized_return_pct": annualized_return_pct(total_ret, days_held, config),
            "hysa_period_return_pct": hysa_ret,
            "alpha_period_pct": total_ret - hysa_ret,
        })

    result = pl.DataFrame(rows)
    return result if was_eager else result.lazy()


def portfolio_alpha_pct(returns_df: pl.DataFrame | pl.LazyFrame) -> float:
    """Compute the dollar-weighted average alpha across all trades.

    Uses period alpha (not annualized) deliberately: annualizing a 1-day
    trade produces absurd numbers that would let noise dominate a weighted
    average. Period alpha stays honest regardless of hold length.

    Parameters
    ----------
    returns_df
        A returns table (see `build_returns_table`), with `alpha_period_pct` and `usd_spent` columns.

    Returns
    -------
    float
        The `usd_spent`-weighted average of `alpha_period_pct`.
    """
    weighted = collect_if_lazy(
        returns_df.select(
            weighted_alpha=(pl.col("alpha_period_pct") * pl.col("usd_spent")).sum(),
            total_spent=pl.col("usd_spent").sum(),
        )
    )
    return float(weighted["weighted_alpha"].item() / weighted["total_spent"].item())


def fit_trend(x: np.ndarray, y: np.ndarray, config: AppConfig) -> tuple[np.ndarray, np.ndarray]:
    """Fit a simple trend curve through (x, y).

    Parameters
    ----------
    x
        The x-coordinates.
    y
        The y-coordinates.
    config
        Application configuration; `config.returns.trend_fit_kind` is read
        (`"linear"` for a least-squares line, `"mean"` for a flat mean).

    Returns
    -------
    tuple[numpy.ndarray, numpy.ndarray]
        `(x_sorted, y_fit)`, ready to overlay on a scatter plot.
    """
    order = np.argsort(x)
    x_sorted = np.asarray(x)[order]
    if config.returns.trend_fit_kind == "mean":
        y_fit = np.full_like(x_sorted, fill_value=float(np.mean(y)), dtype=float)
    else:
        slope, intercept = np.polyfit(x, y, 1)
        y_fit = slope * x_sorted + intercept
    return x_sorted, y_fit
