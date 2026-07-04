"""Dashboard aggregation layer — the API's only source of computed data.

Combines `ledger.*` (replay, lots, metrics, counterfactuals, nav) and
`market_data.*` into the exact shapes `api.py` serves over HTTP; `api.py`
itself does no aggregation, matching `docs/architecture.md`'s split
between the layer that computes something and the layer that serializes
it. `DashboardSettings` is the one piece of state that lives here rather
than in the ledger: a user-set target allocation has no home in an
append-only record of what actually happened (NEW_TASKS.md 0.1), so it is
its own small, persisted, user-editable settings file instead.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from trades.ledger.counterfactuals import (
    benchmark_counterfactual_series,
    hysa_counterfactual_series,
    hysa_counterfactual_value,
)
from trades.ledger.metrics import lot_returns, max_drawdown, realized_gain_total, symbol_metrics, unrealized_gain, xirr
from trades.ledger.nav import growth_of_100, nav_series, period_pnl, time_weighted_return
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger
from trades.market_data import cpi as cpi_module
from trades.market_data import prices
from trades.utils.frames import collect_if_lazy
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from trades.config import AppConfig
    from trades.ledger.nav import PeriodReturn


class DashboardSettings(BaseModel):
    """User-editable dashboard settings, persisted outside the ledger (NEW_TASKS.md 6.5)."""

    model_config = ConfigDict(frozen=True)

    target_allocation_pct: dict[str, float] = Field(default_factory=dict)


def load_settings(config: AppConfig) -> DashboardSettings:
    """Read the persisted dashboard settings, or the defaults if none have been saved yet.

    Parameters
    ----------
    config
        Application configuration; `config.dashboard.settings_path` is read.

    Returns
    -------
    DashboardSettings
        The persisted settings, or `DashboardSettings()` if `settings_path` doesn't exist yet.
    """
    if not config.dashboard.settings_path.exists():
        return DashboardSettings()
    return DashboardSettings.model_validate_json(config.dashboard.settings_path.read_text())


def save_settings(settings: DashboardSettings, config: AppConfig) -> None:
    """Persist dashboard settings, overwriting whatever was saved before.

    Parameters
    ----------
    settings
        The settings to persist.
    config
        Application configuration; `config.dashboard.settings_path` is written to.
    """
    write_json_atomic(settings.model_dump(), config.dashboard.settings_path)


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

    The backbone series for the dollar chart (NEW_TASKS.md 6.2), the NAV
    series (3.1), growth-of-100 (3.3), monthly P&L (3.5), and max drawdown
    (6.7) — all derived from the same day-by-day valuation rather than
    each recomputing it. Replays the ledger truncated to each day (see
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


@dataclass(frozen=True)
class OverviewCards:
    """Headline portfolio stats for the overview card row (NEW_TASKS.md 6.1)."""

    as_of: date
    value_usd: float
    gain_usd: float
    gain_pct: float | None
    realized_gain_usd: float
    unrealized_gain_usd: float
    xirr_pct: float | None
    xirr_is_provisional: bool
    dollar_alpha_vs_hysa_usd: float
    twr_pct: float | None
    twr_annualized_pct: float | None
    timing_gap_pct: float | None


def _hysa_rate_lookup(config: AppConfig) -> Callable[[date], float]:
    def rate(_day: date) -> float:
        return config.returns.hysa_annual_rate

    return rate


def _xirr_and_twr(
    ledger: pl.DataFrame,
    flows: pl.DataFrame,
    price_lookup: Callable[[str, date], float | None],
    value: float,
    as_of: date,
    config: AppConfig,
) -> tuple[float, bool, PeriodReturn]:
    """Compute portfolio XIRR and TWR since the first external flow.

    Extracted out of `overview_cards` purely to keep that function's
    local-variable count down; the two are computed together because both
    need the same `first_flow_date`.

    Returns
    -------
    tuple[float, bool, PeriodReturn]
        XIRR as a percentage, whether it's provisional (< 12 months of
        history), and the TWR since the first external flow.
    """
    first_flow_date = cast("date", min(flows["event_datetime"].dt.date().to_list()))
    dates = [*flows["event_datetime"].dt.date().to_list(), as_of]
    amounts = [*flows["amount"].to_list(), value]
    xirr_pct = xirr(dates, amounts) * 100
    is_provisional = (as_of - first_flow_date).days < config.returns.annualization_days

    daily_values = daily_portfolio_values(ledger, price_lookup, first_flow_date, as_of, config)
    nav = nav_series(daily_values, flows)
    twr = time_weighted_return(nav, start=first_flow_date, end=as_of, config=config)
    return xirr_pct, is_provisional, twr


def overview_cards(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> OverviewCards:
    """Assemble the overview card row: value, gain split, XIRR, dollar alpha, TWR (NEW_TASKS.md 6.1).

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    as_of
        The date to value the portfolio as of.

    Returns
    -------
    OverviewCards
        The headline stats, ready to serialize.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    flows = collect_if_lazy(external_cashflows(ledger))

    realized = realized_gain_total(result.closed_lots)
    unrealized = unrealized_gain(result.open_lots, price_lookup, as_of)
    gain = realized + unrealized
    net_invested = -float(flows["amount"].sum()) if not flows.is_empty() else 0.0
    gain_pct = (gain / net_invested * 100) if net_invested else None

    xirr_pct: float | None = None
    is_provisional = False
    twr: PeriodReturn | None = None
    if not flows.is_empty():
        xirr_pct, is_provisional, twr = _xirr_and_twr(ledger, flows, price_lookup, value, as_of, config)

    hysa_value = hysa_counterfactual_value(flows, as_of, _hysa_rate_lookup(config)) if not flows.is_empty() else 0.0
    timing_gap_pct = (
        xirr_pct - twr.annualized_pct if xirr_pct is not None and twr and twr.annualized_pct is not None else None
    )

    return OverviewCards(
        as_of=as_of,
        value_usd=value,
        gain_usd=gain,
        gain_pct=gain_pct,
        realized_gain_usd=realized,
        unrealized_gain_usd=unrealized,
        xirr_pct=xirr_pct,
        xirr_is_provisional=is_provisional,
        dollar_alpha_vs_hysa_usd=value - hysa_value,
        twr_pct=twr.raw_pct if twr else None,
        twr_annualized_pct=twr.annualized_pct if twr else None,
        timing_gap_pct=timing_gap_pct,
    )


def reallocation_markers(ledger: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Find dates where a sell funded a same-day buy of a different symbol (NEW_TASKS.md 6.2).

    The ledger has no explicit "this sell and that buy were one
    reallocation decision" link (see `counterfactuals.decision_counterfactual_value`,
    which instead takes explicit event IDs from the caller); for chart
    markers, a same-day `SELL` + `BUY` is a reasonable heuristic for "this
    was a reallocation, not independent trades."

    Parameters
    ----------
    ledger
        The ledger.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `sold_symbols`, `bought_symbols` (each a list of
        symbols), one row per date with both a `SELL` and a `BUY`.
    """
    events = collect_if_lazy(ledger).with_columns(event_date=pl.col("event_datetime").dt.date())
    sells = (
        events.filter(pl.col("event_type") == "SELL").group_by("event_date").agg(sold_symbols=pl.col("symbol").unique())
    )
    buys = (
        events
        .filter(pl.col("event_type") == "BUY")
        .group_by("event_date")
        .agg(bought_symbols=pl.col("symbol").unique())
    )
    return sells.join(buys, on="event_date", how="inner").rename({"event_date": "date"}).sort("date")


def _cumulative_contributions(flows: pl.DataFrame, dates: pl.Series) -> pl.DataFrame:
    """Step-line of net external contributions (deposits positive) across a set of dates.

    A step-line, not a smooth line, by construction: it only moves on a
    date with an external flow and holds flat otherwise (NEW_TASKS.md 6.2).

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value` — cumulative net contributions as of each date in `dates`.
    """
    date_frame = pl.DataFrame({"date": dates}).sort("date")
    if flows.is_empty():
        return date_frame.with_columns(value=pl.lit(0.0))

    daily = (
        flows
        .with_columns(flow_date=pl.col("event_datetime").dt.date())
        .group_by("flow_date")
        .agg(net=-pl.col("amount").sum())
        .sort("flow_date")
        .with_columns(cumulative=pl.col("net").cum_sum())
    )
    return (
        date_frame
        .join(daily.select(date=pl.col("flow_date"), cumulative=pl.col("cumulative")), on="date", how="left")
        .with_columns(value=pl.col("cumulative").fill_null(strategy="forward").fill_null(0.0))
        .select("date", "value")
    )


def dollar_chart_series(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> pl.DataFrame:
    """Build the three/four-line dollar chart series (NEW_TASKS.md 6.2).

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration; `config.returns.benchmark_symbol` is read.
    start
        First day of the chart, inclusive.
    end
        Last day of the chart, inclusive.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `contributions_usd`, `portfolio_value_usd`,
        `hysa_value_usd`, `benchmark_value_usd`.
    """
    raw_lookup = make_price_lookup(config)
    adjusted_lookup = make_price_lookup(config, adjusted=True)
    benchmark_symbol = config.returns.benchmark_symbol

    daily_values = daily_portfolio_values(ledger, raw_lookup, start, end, config)
    flows = collect_if_lazy(external_cashflows(ledger))
    contributions = _cumulative_contributions(flows, daily_values["date"])
    hysa_series = hysa_counterfactual_series(flows, end, _hysa_rate_lookup(config))
    benchmark_series = benchmark_counterfactual_series(flows, end, lambda day: adjusted_lookup(benchmark_symbol, day))

    return (
        daily_values
        .rename({"value": "portfolio_value_usd"})
        .join(contributions.rename({"value": "contributions_usd"}), on="date", how="left")
        .join(hysa_series.rename({"value": "hysa_value_usd"}), on="date", how="left")
        .join(benchmark_series.rename({"value": "benchmark_value_usd"}), on="date", how="left")
        .fill_null(0.0)
        .sort("date")
    )


def _cpi_series(dates: pl.Series, config: AppConfig) -> pl.DataFrame:
    """Look up the CPI index for a set of dates, dropping dates before the series starts.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value`.
    """
    history = cpi_module.load_cpi_cache(config)
    values = [cpi_module.cpi_as_of(history, day) for day in dates.to_list()]
    return pl.DataFrame({"date": dates, "value": values}).drop_nulls("value")


def growth_of_100_chart(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> pl.DataFrame:
    """Build the growth-of-$100 chart: your NAV plus every benchmark, indexed to a common start (NEW_TASKS.md 3.3, 6.3).

    Because everything is reindexed to 100 at its own start, no cashflow
    matching is needed here, unlike the dollar chart — this is the one
    chart where benchmarks overlay directly against your own performance.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration; `config.returns.benchmark_symbol` is read.
    start
        First day of the chart, inclusive.
    end
        Last day of the chart, inclusive.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `portfolio_index`, `benchmark_index`, `hysa_index`, `cpi_index`.
    """
    raw_lookup = make_price_lookup(config)
    adjusted_lookup = make_price_lookup(config, adjusted=True)
    benchmark_symbol = config.returns.benchmark_symbol

    daily_values = daily_portfolio_values(ledger, raw_lookup, start, end, config)
    flows = collect_if_lazy(external_cashflows(ledger))
    nav = cast("pl.DataFrame", nav_series(daily_values, flows))
    hysa_index = cast(
        "pl.DataFrame", growth_of_100(hysa_counterfactual_series(flows, end, _hysa_rate_lookup(config)), "value")
    )
    benchmark_index = cast(
        "pl.DataFrame",
        growth_of_100(
            benchmark_counterfactual_series(flows, end, lambda day: adjusted_lookup(benchmark_symbol, day)), "value"
        ),
    )
    cpi_index = cast("pl.DataFrame", growth_of_100(_cpi_series(daily_values["date"], config), "value"))

    return (
        nav
        .select("date", portfolio_index="nav")
        .join(hysa_index.select("date", hysa_index="index"), on="date", how="left")
        .join(benchmark_index.select("date", benchmark_index="index"), on="date", how="left")
        .join(cpi_index.select("date", cpi_index="index"), on="date", how="left")
        .sort("date")
    )


def _month_boundaries(start: date, end: date) -> list[tuple[date, date]]:
    """Split a date range into (first day, last day) pairs, one per calendar month.

    Returns
    -------
    list[tuple[datetime.date, datetime.date]]
        One `(month_start, month_end)` pair per calendar month, clipped to `[start, end]`.
    """
    boundaries: list[tuple[date, date]] = []
    current = start.replace(day=1)
    while current <= end:
        last_day_of_month = monthrange(current.year, current.month)[1]
        month_end = min(current.replace(day=last_day_of_month), end)
        boundaries.append((current, month_end))
        current = month_end + timedelta(days=1)
    return boundaries


def monthly_pnl(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> pl.DataFrame:
    """Split each month's value change into contributions and actual market gain (NEW_TASKS.md 3.5, 6.4).

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    start
        First day to report, inclusive.
    end
        Last day to report, inclusive.

    Returns
    -------
    polars.DataFrame
        Columns `month` (`"YYYY-MM"`), `contributions_usd`, `market_gain_usd`.
    """
    price_lookup = make_price_lookup(config)
    flows = collect_if_lazy(external_cashflows(ledger))

    months: list[str] = []
    contributions: list[float] = []
    market_gains: list[float] = []
    for month_start, month_end in _month_boundaries(start, end):
        value_start = portfolio_value(
            replay_ledger(ledger.filter(pl.col("event_datetime").dt.date() < month_start), config),
            price_lookup,
            month_start - timedelta(days=1),
        )
        value_end = portfolio_value(
            replay_ledger(ledger.filter(pl.col("event_datetime").dt.date() <= month_end), config),
            price_lookup,
            month_end,
        )
        month_flows = flows.filter(pl.col("event_datetime").dt.date().is_between(month_start, month_end))
        net_contribution = -float(month_flows["amount"].sum()) if not month_flows.is_empty() else 0.0

        months.append(month_start.strftime("%Y-%m"))
        contributions.append(net_contribution)
        market_gains.append(period_pnl(flows, value_start, value_end, month_start, month_end))

    return pl.DataFrame({"month": months, "contributions_usd": contributions, "market_gain_usd": market_gains})


def allocation_view(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> pl.DataFrame:
    """Current-value allocation by symbol (including cash), against a user-set target (NEW_TASKS.md 6.5).

    Sliced by current value, not invested dollars — invested-dollar slices
    can't show drift from a target allocation.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    as_of
        The date to value holdings as of.

    Returns
    -------
    polars.DataFrame
        Columns `symbol`, `value_usd`, `current_pct`, `target_pct`, `drift_pct`.

    Raises
    ------
    ValueError
        If a price is unavailable for any symbol still held.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    settings = load_settings(config)

    if result.open_lots.is_empty():
        holdings = pl.DataFrame(schema={"symbol": pl.Utf8, "value_usd": pl.Float64})
    else:
        by_symbol = result.open_lots.group_by("symbol").agg(shares=pl.col("shares").sum())
        values = []
        for symbol, symbol_shares in zip(by_symbol["symbol"].to_list(), by_symbol["shares"].to_list(), strict=True):
            price = price_lookup(symbol, as_of)
            if price is None:
                message = f"No price available for {symbol} on or before {as_of}."
                raise ValueError(message)
            values.append(symbol_shares * price)
        holdings = pl.DataFrame({"symbol": by_symbol["symbol"], "value_usd": values})

    cash_row = pl.DataFrame({"symbol": [config.ledger.cash_symbol], "value_usd": [result.cash_balance]})
    combined = pl.concat([holdings, cash_row], how="vertical")
    total = float(combined["value_usd"].sum())
    target = settings.target_allocation_pct

    return (
        combined
        .with_columns(
            current_pct=(pl.col("value_usd") / total * 100) if total else pl.lit(0.0),
            target_pct=pl.col("symbol").replace_strict(target, default=0.0, return_dtype=pl.Float64),
        )
        .with_columns(drift_pct=pl.col("current_pct") - pl.col("target_pct"))
        .sort("value_usd", descending=True)
    )


@dataclass(frozen=True)
class LotsTable:
    """Open lots (with returns), closed lots (with vs-HYSA alpha), and a per-symbol rollup (NEW_TASKS.md 6.6)."""

    open_lots: pl.DataFrame
    closed_lots: pl.DataFrame
    symbol_rollup: pl.DataFrame


def _closed_lots_with_hysa_alpha(closed_lots: pl.DataFrame, config: AppConfig) -> pl.DataFrame:
    """Add a total-return and vs-HYSA alpha to every closed lot, over its actual holding window.

    A closed lot's window is finished, so this alpha is a legitimate,
    non-provisional number (unlike a live position's annualized return,
    which is gated per NEW_TASKS.md 1.2).

    Returns
    -------
    polars.DataFrame
        `closed_lots` plus `days_held`, `total_return_pct`, `alpha_vs_hysa_pct`.
    """
    if closed_lots.is_empty():
        return closed_lots.with_columns(
            days_held=pl.lit(None, dtype=pl.Int64),
            total_return_pct=pl.lit(None, dtype=pl.Float64),
            alpha_vs_hysa_pct=pl.lit(None, dtype=pl.Float64),
        )
    annualization_days = config.returns.annualization_days
    hysa_rate = config.returns.hysa_annual_rate
    return closed_lots.with_columns(
        days_held=(pl.col("closed_at").dt.date() - pl.col("opened_at").dt.date()).dt.total_days(),
        total_return_pct=(
            (pl.col("realized_gain") + pl.col("dividends_received")) / (pl.col("shares") * pl.col("cost_per_share"))
        )
        * 100,
    ).with_columns(
        alpha_vs_hysa_pct=pl.col("total_return_pct")
        - (((1 + hysa_rate) ** (pl.col("days_held") / annualization_days) - 1) * 100)
    )


def lots_table(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> LotsTable:
    """Assemble the trade-level table: open lots, closed lots, per-symbol rollup (NEW_TASKS.md 6.6).

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    as_of
        The date to price open lots as of.

    Returns
    -------
    LotsTable
        Open lots, closed lots, and the per-symbol rollup.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)

    open_lots = (
        cast("pl.DataFrame", lot_returns(result.open_lots, price_lookup, as_of, config))
        if not result.open_lots.is_empty()
        else result.open_lots
    )
    closed_lots = _closed_lots_with_hysa_alpha(result.closed_lots, config)

    symbols = sorted({*result.open_lots["symbol"].to_list(), *result.closed_lots["symbol"].to_list()})
    rollup_rows = [asdict(symbol_metrics(ledger, result, symbol, price_lookup, as_of)) for symbol in symbols]
    symbol_rollup = pl.DataFrame(rollup_rows) if rollup_rows else pl.DataFrame()

    return LotsTable(open_lots=open_lots, closed_lots=closed_lots, symbol_rollup=symbol_rollup)


def risk_stat(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> float:
    """Largest peak-to-trough decline in the NAV series, as a percentage (NEW_TASKS.md 6.7).

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    start
        First day of the window, inclusive.
    end
        Last day of the window, inclusive.

    Returns
    -------
    float
        The max drawdown, as a percentage (0 or negative).
    """
    raw_lookup = make_price_lookup(config)
    daily_values = daily_portfolio_values(ledger, raw_lookup, start, end, config)
    flows = collect_if_lazy(external_cashflows(ledger))
    nav = cast("pl.DataFrame", nav_series(daily_values, flows))
    return max_drawdown(nav, "nav") * 100


def data_quality(symbols: Sequence[str], config: AppConfig) -> pl.DataFrame:
    """Last cached price date per symbol, for the data-quality panel (NEW_TASKS.md 6.9).

    Parameters
    ----------
    symbols
        The symbols to report on.
    config
        Application configuration; `config.prices.cache_dir` is read.

    Returns
    -------
    polars.DataFrame
        Columns `symbol`, `last_price_date`.
    """
    last_dates: list[date | None] = []
    for symbol in symbols:
        history = prices.load_price_cache(symbol, config)
        last_dates.append(cast("date", history["price_date"].max()) if not history.is_empty() else None)
    return pl.DataFrame({"symbol": list(symbols), "last_price_date": last_dates})
