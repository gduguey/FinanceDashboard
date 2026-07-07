"""Detect uninvested cash sitting idle, and estimate what it's missed out on.

`daily_cash_balances` mirrors `valuation.daily_portfolio_values`'s
event-date-then-forward-fill walk, but tracks `ReplayResult.cash_balance`
instead of the priced total — no price lookup needed, since cash needs no
pricing. `sitting_since_date`/`cash_sitting_summary` are pure arithmetic
over that series plus a pre-computed growth-of-100 index (see
`charts.growth_of_100_chart`), so neither touches the ledger or a price
cache directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast

import polars as pl

from trades.ledger.replay import replay_ledger

if TYPE_CHECKING:
    from datetime import date

    from trades.config import AppConfig

WarningLevel = Literal["none", "light", "heavy"]


def daily_cash_balances(
    ledger: pl.DataFrame | pl.LazyFrame, config: AppConfig, start: date, end: date
) -> pl.DataFrame | pl.LazyFrame:
    """Compute the uninvested cash balance for every calendar day in a range.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration, passed through to `replay_ledger`.
    start
        First day to value, inclusive.
    end
        Last day to value, inclusive.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `date`, `cash`, one row per calendar day in `[start, end]`.
        Same type as input.
    """
    was_eager = isinstance(ledger, pl.DataFrame)
    ledger_df = ledger if was_eager else ledger.collect()

    event_dates_result = ledger_df.filter(
        (pl.col("event_datetime").dt.date() >= start) & (pl.col("event_datetime").dt.date() <= end)
    ).select(pl.col("event_datetime").dt.date().unique().sort())
    event_dates: list[date] = [] if event_dates_result.is_empty() else event_dates_result["event_datetime"].to_list()

    cash_by_date: dict[date, float] = {}
    for event_date in event_dates:
        result = replay_ledger(ledger_df.filter(pl.col("event_datetime").dt.date() <= event_date), config)
        cash_by_date[event_date] = result.cash_balance

    all_dates = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    cash: list[float] = []
    for cal_date in all_dates:
        recent_event_dates = [d for d in event_dates if d <= cal_date]
        cash.append(cash_by_date[max(recent_event_dates)] if recent_event_dates else 0.0)

    result = pl.DataFrame({"date": all_dates, "cash": cash})
    return result if was_eager else result.lazy()


def sitting_since_date(daily_cash: pl.DataFrame, threshold_pct: float) -> date:
    """Find the most recent date cash dropped by at least `threshold_pct` from the previous day.

    An increase never resets this, however large — only a decrease that
    clears the threshold, relative to the immediately preceding day's own
    balance (which already reflects any deposits since the last reset).

    Parameters
    ----------
    daily_cash
        Columns `date`, `cash`, sorted ascending by date, as returned by
        `daily_cash_balances`.
    threshold_pct
        Minimum fractional drop from the previous day's balance that
        counts as a real deployment (see `config.CashSittingConfig`).

    Returns
    -------
    datetime.date
        The date the current cash balance has been sitting since — the
        series' first date if no qualifying decrease has ever occurred.
    """
    dates = daily_cash["date"].to_list()
    cash = daily_cash["cash"].to_list()
    since = dates[0]
    previous = cash[0]
    for day, amount in zip(dates[1:], cash[1:], strict=True):
        if amount < previous * (1 - threshold_pct):
            since = day
        previous = amount
    return cast("date", since)


@dataclass(frozen=True)
class CashSittingSummary:
    """Current uninvested cash, how long it's been idle, and what it's missed out on."""

    cash_usd: float
    sitting_since: date
    days_sitting: int
    warning_level: WarningLevel
    hypothetical_value_portfolio_usd: float
    missed_earnings_portfolio_usd: float
    hypothetical_value_benchmark_usd: float
    missed_earnings_benchmark_usd: float


def cash_sitting_summary(
    daily_cash: pl.DataFrame,
    growth_index: pl.DataFrame,
    as_of: date,
    config: AppConfig,
) -> CashSittingSummary:
    """Summarize the current cash balance's idle time and its missed growth vs. the portfolio and benchmark.

    The missed-earnings estimate applies the portfolio's and benchmark's
    own time-weighted growth (see `charts.growth_of_100_chart`) over the
    sitting window to the current cash amount — "what this cash would be
    worth now had it grown at the same rate as everything else already
    invested," not a replay of a specific hypothetical purchase.

    Parameters
    ----------
    daily_cash
        Columns `date`, `cash`, as returned by `daily_cash_balances`,
        covering at least `[sitting_since, as_of]`.
    growth_index
        Columns `date`, `portfolio_index`, `benchmark_index`, as returned
        by `charts.growth_of_100_chart`, covering at least the same range.
    as_of
        The date to report the current cash balance and warning as of.
    config
        Application configuration; `config.cash_sitting` is read.

    Returns
    -------
    CashSittingSummary
    """
    cash_row = daily_cash.filter(pl.col("date") == as_of)
    cash_usd = float(cash_row["cash"][0])

    since = sitting_since_date(daily_cash.filter(pl.col("date") <= as_of), config.cash_sitting.decrease_threshold_pct)
    days_sitting = (as_of - since).days

    if days_sitting > config.cash_sitting.heavy_warning_days:
        warning_level: WarningLevel = "heavy"
    elif days_sitting > config.cash_sitting.light_warning_days:
        warning_level = "light"
    else:
        warning_level = "none"

    start_row = growth_index.filter(pl.col("date") == since)
    end_row = growth_index.filter(pl.col("date") == as_of)
    portfolio_growth = float(end_row["portfolio_index"][0]) / float(start_row["portfolio_index"][0])
    benchmark_growth = float(end_row["benchmark_index"][0]) / float(start_row["benchmark_index"][0])

    hypothetical_portfolio = cash_usd * portfolio_growth
    hypothetical_benchmark = cash_usd * benchmark_growth

    return CashSittingSummary(
        cash_usd=cash_usd,
        sitting_since=since,
        days_sitting=days_sitting,
        warning_level=warning_level,
        hypothetical_value_portfolio_usd=hypothetical_portfolio,
        missed_earnings_portfolio_usd=hypothetical_portfolio - cash_usd,
        hypothetical_value_benchmark_usd=hypothetical_benchmark,
        missed_earnings_benchmark_usd=hypothetical_benchmark - cash_usd,
    )
