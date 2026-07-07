"""Detect uninvested cash sitting idle, and estimate what it's missed out on.

`daily_cash_balances` mirrors `valuation.daily_portfolio_values`'s
event-date-then-forward-fill walk, but tracks `ReplayResult.cash_balance`
instead of the priced total — no price lookup needed, since cash needs no
pricing.

Every dollar of cash is tracked as its own `CashLot` from the day it
arrives until a later outflow FIFO-consumes it (oldest first) —
`open_cash_lots` replays the daily series into whichever lots are still
open at the end. This is the one thing a single running "sitting since"
scalar can't do: a fresh deposit landing on top of already-sitting cash
gets its own lot, dated from when it actually arrived, rather than being
silently absorbed into whatever older cash happened to be sitting next to
it. `cash_sitting_summary` (the card's as-of-today snapshot) and
`cash_received_counterfactual` (the chart's full daily series) both build
on the same lot model — the summary reports the oldest open lot's age and
sums each lot's own missed growth; the chart values every currently-open
lot ("live") and, the moment a lot is consumed, banks its gain into a
separate "realized" total that's frozen from then on rather than left to
keep compounding — continuing to grow it after the money's actually
invested would double-count against the real portfolio's own subsequent
performance, which is already tracked by the dollar chart's own
counterfactuals (see `ledger.counterfactuals`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import polars as pl

from trades.ledger.counterfactuals import hysa_counterfactual_series
from trades.ledger.replay import replay_ledger
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig

WarningLevel = Literal["none", "light", "heavy"]

_EPSILON = 1e-9


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


@dataclass(frozen=True)
class CashLot:
    """One inflow into the uninvested cash balance, tracked until a later outflow consumes it."""

    arrival_date: date
    amount: float


def _consume(lots: deque[CashLot], amount: float) -> list[tuple[date, float]]:
    """Remove `amount` from the front of `lots` (oldest first), mutating it in place.

    Parameters
    ----------
    lots
        Currently-open lots, oldest first; mutated in place.
    amount
        Dollar amount to consume, oldest lots first.

    Returns
    -------
    list[tuple[datetime.date, float]]
        `(lot.arrival_date, amount_consumed)` for every lot this outflow touched, oldest first.
    """
    consumed: list[tuple[date, float]] = []
    remaining = amount
    while remaining > _EPSILON and lots:
        lot = lots[0]
        taken = min(lot.amount, remaining)
        consumed.append((lot.arrival_date, taken))
        remaining -= taken
        if taken >= lot.amount - _EPSILON:
            lots.popleft()
        else:
            lots[0] = CashLot(lot.arrival_date, lot.amount - taken)
    return consumed


def open_cash_lots(daily_cash: pl.DataFrame) -> list[CashLot]:
    """Replay a daily cash series into FIFO lots, returning whichever ones are still open at its last day.

    Every day the balance increases, that increase becomes a new lot
    dated that day. Every day it decreases, the decrease consumes the
    oldest open lot(s) first — never treated as "noise" below some
    threshold, since any decrease is a real, exact dollar amount leaving
    the balance. A deposit never touches an existing lot's date, however
    large — see the module docstring for why that matters.

    Parameters
    ----------
    daily_cash
        Columns `date`, `cash`, sorted ascending by date.

    Returns
    -------
    list[CashLot]
        Whichever lots are still open at the series' last day, oldest first.
    """
    dates = daily_cash["date"].to_list()
    cash = daily_cash["cash"].to_list()
    lots: deque[CashLot] = deque()
    previous = 0.0
    for day, amount in zip(dates, cash, strict=True):
        delta = amount - previous
        if delta > _EPSILON:
            lots.append(CashLot(day, delta))
        elif delta < -_EPSILON:
            _consume(lots, -delta)
        previous = amount
    return list(lots)


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

    `sitting_since`/`days_sitting` report the *oldest* open lot (see
    `open_cash_lots`) — the worst case, not a blend, so a big fresh
    deposit can never mask a small stale pocket sitting next to it. The
    missed-earnings estimate sums each lot's own time-weighted growth
    (see `charts.growth_of_100_chart`) from its own arrival date to
    `as_of`, rather than applying one blended ratio to the whole balance —
    freshly-arrived cash correctly contributes ~$0 of missed earnings, not
    a share of older cash's.

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

    lots = open_cash_lots(daily_cash.filter(pl.col("date") <= as_of))
    sitting_since = lots[0].arrival_date if lots else as_of
    days_sitting = (as_of - sitting_since).days

    if days_sitting > config.cash_sitting.heavy_warning_days:
        warning_level: WarningLevel = "heavy"
    elif days_sitting > config.cash_sitting.light_warning_days:
        warning_level = "light"
    else:
        warning_level = "none"

    growth_dates = growth_index["date"].to_list()
    portfolio_index_at = dict(zip(growth_dates, growth_index["portfolio_index"].to_list(), strict=True))
    benchmark_index_at = dict(zip(growth_dates, growth_index["benchmark_index"].to_list(), strict=True))
    end_portfolio = portfolio_index_at[as_of]
    end_benchmark = benchmark_index_at[as_of]

    hypothetical_portfolio = sum(
        (lot.amount * end_portfolio / portfolio_index_at[lot.arrival_date] for lot in lots), 0.0
    )
    hypothetical_benchmark = sum(
        (lot.amount * end_benchmark / benchmark_index_at[lot.arrival_date] for lot in lots), 0.0
    )

    return CashSittingSummary(
        cash_usd=cash_usd,
        sitting_since=sitting_since,
        days_sitting=days_sitting,
        warning_level=warning_level,
        hypothetical_value_portfolio_usd=hypothetical_portfolio,
        missed_earnings_portfolio_usd=hypothetical_portfolio - cash_usd,
        hypothetical_value_benchmark_usd=hypothetical_benchmark,
        missed_earnings_benchmark_usd=hypothetical_benchmark - cash_usd,
    )


def _hysa_index_lookup(
    start: date, end: date, rate_lookup: Callable[[date], float], days_per_year: int
) -> Callable[[date], float]:
    """Build a $1-since-`start` compounding index, returned as a same-day lookup.

    Reuses `counterfactuals.hysa_counterfactual_series` (fed a single
    synthetic $1 deposit on `start`) rather than re-deriving daily
    compounding here — `index(t) / index(lot_date)` is then the HYSA
    growth factor from `lot_date` to `t`, the exact role a raw price
    lookup already plays for the benchmark side.

    Returns
    -------
    Callable[[datetime.date], float]
        Looks up the compounding index's value for any date in `[start, end]`.
    """
    principal = pl.DataFrame({"event_datetime": [datetime.combine(start, datetime.min.time())], "amount": [-1.0]})
    index = collect_if_lazy(hysa_counterfactual_series(principal, end, rate_lookup, days_per_year))
    values = dict(zip(index["date"].to_list(), index["value"].to_list(), strict=True))
    return lambda day: values[day]


def cash_lot_counterfactual(daily_cash: pl.DataFrame, index_lookup: Callable[[date], float]) -> pl.DataFrame:
    """Value every currently-open cash lot against a growth index, banking each lot's gain once it's consumed.

    For every day: `live_usd` is the current worth of whichever lots are
    still open (bounded, tracks the real cash balance's own shape —
    converges to it whenever cash hits $0). `realized_usd` is a running
    total that only moves in discrete jumps, one per (partial or full)
    lot consumption, each sized to that slice's own growth from its
    arrival date to the day it was consumed — then stays flat until the
    next consumption. See the module docstring for why it's frozen rather
    than left compounding.

    Parameters
    ----------
    daily_cash
        Columns `date`, `cash`, sorted ascending by date, as returned by
        `daily_cash_balances` — every date in its range gets a row in the
        result.
    index_lookup
        Looks up a growth index's value for a given date — the
        benchmark's own adjusted price, or a HYSA compounding index (see
        `_hysa_index_lookup`). Must cover every date in `daily_cash`.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `live_usd`, `realized_usd`, one row per day in `daily_cash`.
    """
    dates = daily_cash["date"].to_list()
    cash = daily_cash["cash"].to_list()
    lots: deque[CashLot] = deque()
    previous = 0.0
    realized = 0.0
    out_dates: list[date] = []
    live_values: list[float] = []
    realized_values: list[float] = []
    for day, amount in zip(dates, cash, strict=True):
        delta = amount - previous
        today_index = index_lookup(day)
        if delta > _EPSILON:
            lots.append(CashLot(day, delta))
        elif delta < -_EPSILON:
            for lot_date, consumed_amount in _consume(lots, -delta):
                realized += consumed_amount * (today_index / index_lookup(lot_date) - 1)
        previous = amount
        out_dates.append(day)
        live_values.append(sum((lot.amount * today_index / index_lookup(lot.arrival_date) for lot in lots), 0.0))
        realized_values.append(realized)
    return pl.DataFrame({"date": out_dates, "live_usd": live_values, "realized_usd": realized_values})


def cash_received_counterfactual(
    daily_cash: pl.DataFrame,
    benchmark_price_lookup: Callable[[date], float | None],
    hysa_rate_lookup: Callable[[date], float],
    days_per_year: int,
) -> pl.DataFrame:
    """For every day, value currently-sitting cash against the benchmark/HYSA, plus what's already been banked.

    The chart-series counterpart to `cash_sitting_summary`'s single as-of
    snapshot — built on the same `CashLot` model via
    `cash_lot_counterfactual`, called once per index (benchmark, HYSA).

    Parameters
    ----------
    daily_cash
        Columns `date`, `cash`, as returned by `daily_cash_balances` —
        every date in its range gets a row in the result.
    benchmark_price_lookup
        Looks up the benchmark's adjusted price as of a given date.
    hysa_rate_lookup
        Looks up the annual HYSA rate as of a given date.
    days_per_year
        Day-count basis for converting the annual HYSA rate to a daily one.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `benchmark_live_usd`, `benchmark_realized_usd`,
        `hysa_live_usd`, `hysa_realized_usd`, one row per day in `daily_cash`.
    """
    dates = daily_cash["date"].to_list()
    start, end = dates[0], dates[-1]

    def _benchmark_index(day: date) -> float:
        price = benchmark_price_lookup(day)
        if price is None:
            message = f"No price available for {day}."
            raise ValueError(message)
        return price

    hysa_index = _hysa_index_lookup(start, end, hysa_rate_lookup, days_per_year)

    benchmark = cash_lot_counterfactual(daily_cash, _benchmark_index)
    hysa = cash_lot_counterfactual(daily_cash, hysa_index)
    return (
        daily_cash
        .select("date")
        .join(
            benchmark.rename({"live_usd": "benchmark_live_usd", "realized_usd": "benchmark_realized_usd"}),
            on="date",
            how="left",
        )
        .join(hysa.rename({"live_usd": "hysa_live_usd", "realized_usd": "hysa_realized_usd"}), on="date", how="left")
        .sort("date")
    )
