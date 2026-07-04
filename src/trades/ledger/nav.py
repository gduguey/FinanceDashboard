"""The NAV (unit) method.

Will hold: the NAV/unit series, time-weighted return, growth-of-$100
indexing, and period P&L decomposition. The
XIRR-vs-TWR timing gap is a bare `xirr - twr` subtraction of two
numbers already produced elsewhere — it gets no dedicated function here,
same call as skipping a `total_gain()` wrapper in `metrics.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from datetime import date

    from trades.config import AppConfig


def nav_series(
    portfolio_values: pl.DataFrame | pl.LazyFrame,
    external_flows: pl.DataFrame | pl.LazyFrame,
) -> pl.DataFrame | pl.LazyFrame:
    """Turn a portfolio value series into a NAV/units series (NEW_TASKS.md 3.1).

    `NAV(t) = value_pre_flow(t) / units_outstanding(t)`, where
    `value_pre_flow` is `portfolio_values`' end-of-day value with that
    day's external flow backed out (`value + amount`, in the
    `DEPOSIT`-negative/`WITHDRAWAL`-positive convention) — computing NAV
    from the post-flow value would let a deposit inflate NAV on the day
    it lands, the ordering bug NEW_TASKS.md 3.1 calls out. Each flow then
    mints or burns units at that pre-flow NAV: `units_minted = deposit /
    NAV_pre_flow`. At inception (no units outstanding yet), NAV bootstraps
    to 100. This is a genuinely sequential walk, like
    `replay.replay_ledger`'s loop, since each day's units depend on every
    prior day's.

    Parameters
    ----------
    portfolio_values
        Columns `date`, `value` — end-of-day portfolio value (holdings +
        cash), in chronological order.
    external_flows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `portfolio_values` with `nav` and `units_outstanding` columns
        added. Same type as `portfolio_values`.
    """
    values = collect_if_lazy(portfolio_values).sort("date")
    was_eager = isinstance(portfolio_values, pl.DataFrame)
    if values.is_empty():
        empty = values.with_columns(
            nav=pl.lit(None, dtype=pl.Float64),
            units_outstanding=pl.lit(None, dtype=pl.Float64),
        )
        return empty if was_eager else empty.lazy()

    flows = collect_if_lazy(external_flows)
    flows_by_date: dict[date, float] = {}
    if not flows.is_empty():
        daily_flows = (
            flows
            .with_columns(flow_date=pl.col("event_datetime").dt.date())
            .group_by("flow_date")
            .agg(amount=pl.col("amount").sum())
        )
        flows_by_date = dict(zip(daily_flows["flow_date"].to_list(), daily_flows["amount"].to_list(), strict=True))

    has_units = False
    units_outstanding = 0.0
    navs: list[float] = []
    units_outstanding_series: list[float] = []
    for row in values.iter_rows(named=True):
        flow_amount = flows_by_date.get(row["date"], 0.0)
        value_pre_flow = row["value"] + flow_amount
        nav = value_pre_flow / units_outstanding if has_units else 100.0
        units_outstanding -= flow_amount / nav
        has_units = True
        navs.append(nav)
        units_outstanding_series.append(units_outstanding)

    result = values.with_columns(
        nav=pl.Series(navs, dtype=pl.Float64),
        units_outstanding=pl.Series(units_outstanding_series, dtype=pl.Float64),
    )
    return result if was_eager else result.lazy()


@dataclass(frozen=True)
class PeriodReturn:
    """A time-weighted return over a period, annualized only once long enough (NEW_TASKS.md 1.2, 3.2)."""

    raw_pct: float
    annualized_pct: float | None


def time_weighted_return(
    nav: pl.DataFrame | pl.LazyFrame,
    start: date,
    end: date,
    config: AppConfig,
) -> PeriodReturn:
    """Compute the time-weighted return between two dates on a NAV series (NEW_TASKS.md 3.2).

    `TWR = NAV_end / NAV_start - 1`. Because NAV already nets out
    contribution timing (`nav.nav_series`), this needs no cashflow
    matching, unlike XIRR. Same annualization gate as `metrics.lot_returns`
    (1.2): below `config.returns.annualization_days`, only the raw figure
    is returned.

    Parameters
    ----------
    nav
        A NAV series, as returned by `nav_series`: columns `date`, `nav`.
    start
        The period's start date.
    end
        The period's end date.
    config
        Application configuration; `config.returns.annualization_days` is read.

    Returns
    -------
    PeriodReturn
        The raw return, and the annualized return if the period is long
        enough to annualize.

    Raises
    ------
    ValueError
        If `nav` has no row for `start` or `end`.
    """
    rows = collect_if_lazy(nav)
    navs_by_date = dict(zip(rows["date"].to_list(), rows["nav"].to_list(), strict=True))
    if start not in navs_by_date or end not in navs_by_date:
        message = f"No NAV available for {start} or {end}."
        raise ValueError(message)

    raw = (navs_by_date[end] / navs_by_date[start] - 1) * 100
    days = (end - start).days
    annualization_days = config.returns.annualization_days
    annualized = ((1 + raw / 100) ** (annualization_days / days) - 1) * 100 if days >= annualization_days else None
    return PeriodReturn(raw_pct=raw, annualized_pct=annualized)


def growth_of_100(series: pl.DataFrame | pl.LazyFrame, value_column: str) -> pl.DataFrame | pl.LazyFrame:
    """Reindex a value series to start at 100, so unrelated series are visually comparable (NEW_TASKS.md 3.3).

    Because everything is indexed to a common starting point, this is
    what makes it possible to overlay your NAV series against VOO, HYSA,
    and CPI series on the same chart without any cashflow matching.

    Parameters
    ----------
    series
        A value series, in chronological order.
    value_column
        The column to reindex.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `series` with an `index` column added, `100` at the first row.
        Same type as `series`.
    """
    return series.with_columns(index=pl.col(value_column) / pl.first(value_column) * 100)


def period_pnl(
    external_flows: pl.DataFrame | pl.LazyFrame,
    value_start: float,
    value_end: float,
    period_start: date,
    period_end: date,
) -> float:
    """Split a period's value change into contributions and actual market gain (NEW_TASKS.md 3.5).

    `market_gain = value_end - value_start - net_contributions`, where
    `net_contributions` flips `external_flows`' `DEPOSIT`-negative/
    `WITHDRAWAL`-positive convention back to the accounting-identity sign
    (deposits positive) so it can be netted against the value change.

    Parameters
    ----------
    external_flows
        External cashflows, as returned by `replay.external_cashflows`:
        columns `event_datetime`, `amount` (`DEPOSIT` negative,
        `WITHDRAWAL` positive).
    value_start
        Portfolio value at `period_start`.
    value_end
        Portfolio value at `period_end`.
    period_start
        The period's start date, inclusive.
    period_end
        The period's end date, inclusive.

    Returns
    -------
    float
        The period's market gain, net of contributions.
    """
    flows = collect_if_lazy(external_flows)
    in_period = flows.filter(pl.col("event_datetime").dt.date().is_between(period_start, period_end))
    net_contributions = -float(in_period["amount"].sum())
    return value_end - value_start - net_contributions
