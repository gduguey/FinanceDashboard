"""Per-trade return math: total return, CAGR-style annualization, a
compounded HYSA benchmark over the same window, and the resulting alpha.

See docs/returns.md for the derivation of each step. Every function that
needs the annualization convention or the benchmark rate takes a
`ReturnsConfig` explicitly — there is no module-level default to fall back to.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import pandas as pd

from trades.config import ReturnsConfig


def total_return_pct(price_paid: float, current_price: float) -> float:
    return (current_price - price_paid) / price_paid * 100


def annualized_return_pct(
    total_return_pct_value: float, days_held: int, config: ReturnsConfig
) -> float:
    """Compound the observed return up to a full year's rate. NaN at
    days_held == 0 (nothing to annualize); note that very short holds still
    produce large-looking numbers by design — that noise is why the combined
    portfolio alpha uses period alpha instead, see `portfolio_alpha_pct`."""
    if days_held < 0:
        raise ValueError(f"days_held must be >= 0, got {days_held} (as_of before the trade date?)")
    if days_held == 0:
        return float("nan")
    growth = 1 + total_return_pct_value / 100
    return (growth ** (config.annualization_days / days_held) - 1) * 100


def hysa_period_return_pct(days_held: int, config: ReturnsConfig) -> float:
    """What a compounding HYSA at `config.hysa_annual_rate` would return over `days_held` days."""
    if days_held < 0:
        raise ValueError(f"days_held must be >= 0, got {days_held} (as_of before the trade date?)")
    return ((1 + config.hysa_annual_rate) ** (days_held / config.annualization_days) - 1) * 100


def build_returns_table(
    trades: pd.DataFrame,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: ReturnsConfig,
) -> pd.DataFrame:
    """One row per trade: current price, days held, total/annualized return,
    the HYSA benchmark over the same window, and the resulting alpha.

    Raises `ValueError` if `price_lookup(symbol, as_of)` returns None for any
    trade — a missing price means the cache hasn't been updated for that
    symbol/date, and silently dropping the trade would produce an
    incomplete table with no indication anything was skipped.
    """
    rows = []
    for record in trades.to_dict("records"):
        symbol = record["symbol"]
        trade_date = record["trade_date"]
        trade_date = trade_date.date() if hasattr(trade_date, "date") else trade_date
        current_price = price_lookup(symbol, as_of)
        if current_price is None:
            raise ValueError(
                f"No price available for {symbol} on or before {as_of}. Update the price "
                "cache (prices.update_price_cache) before building the returns table."
            )
        days_held = (as_of - trade_date).days
        price_paid = record["usd_per_share"]
        total_ret = total_return_pct(price_paid, current_price)
        hysa_ret = hysa_period_return_pct(days_held, config)
        rows.append(
            {
                **record,
                "as_of_date": as_of,
                "current_price": current_price,
                "days_held": days_held,
                "total_return_pct": total_ret,
                "annualized_return_pct": annualized_return_pct(total_ret, days_held, config),
                "hysa_period_return_pct": hysa_ret,
                "alpha_period_pct": total_ret - hysa_ret,
            }
        )
    return pd.DataFrame(rows)


def portfolio_alpha_pct(returns_df: pd.DataFrame) -> float:
    """Dollar-weighted average alpha across all trades.

    Uses period alpha (not annualized) deliberately: annualizing a 1-day
    trade produces absurd numbers that would let noise dominate a weighted
    average. Period alpha stays honest regardless of hold length.
    """
    weights = returns_df["usd_spent"]
    return float((returns_df["alpha_period_pct"] * weights).sum() / weights.sum())


def fit_trend(x: np.ndarray, y: np.ndarray, config: ReturnsConfig) -> tuple[np.ndarray, np.ndarray]:
    """A simple trend curve through (x, y), per `config.trend_fit_kind`: a
    least-squares line, or a flat mean. Returns (x_sorted, y_fit) ready to
    overlay on a scatter plot."""
    order = np.argsort(x)
    x_sorted = np.asarray(x)[order]
    if config.trend_fit_kind == "mean":
        y_fit = np.full_like(x_sorted, fill_value=float(np.mean(y)), dtype=float)
    else:
        slope, intercept = np.polyfit(x, y, 1)
        y_fit = slope * x_sorted + intercept
    return x_sorted, y_fit
