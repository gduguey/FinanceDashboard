"""Dashboard aggregation layer — the API's only source of computed data.

Combines `ledger.*` (replay, lots, metrics, counterfactuals, nav) and
`market_data.*` into the exact shapes `api.py` serves over HTTP; `api.py`
itself does no aggregation, matching `docs/architecture.md`'s split
between the layer that computes something and the layer that serializes
it. `DashboardSettings` is the one piece of state that lives here rather
than in the ledger: a user-set target allocation has no home in an
append-only record of what actually happened, so it is its own small,
persisted, user-editable settings file instead.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from trades.config import TaxRegime
from trades.ledger.counterfactuals import (
    benchmark_counterfactual_series,
    hysa_counterfactual_series,
    hysa_counterfactual_value,
)
from trades.ledger.metrics import lot_returns, max_drawdown, realized_gain_total, symbol_metrics, unrealized_gain, xirr
from trades.ledger.nav import growth_of_100, nav_series, period_pnl, time_weighted_return
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger
from trades.ledger.taxes import (
    after_tax_rate_lookup,
    annual_tax_report,
    flag_wash_sales,
    liquidation_gain_buckets,
    liquidation_tax_usd,
    preview_sale,
    tax_owed_by_year_and_regime,
)
from trades.market_data import cpi as cpi_module
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.utils.frames import collect_if_lazy
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from trades.config import AppConfig
    from trades.ledger.nav import PeriodReturn


class DashboardSettings(BaseModel):
    """User-editable dashboard settings, persisted outside the ledger.

    `hysa_fixed_rate_pct` takes priority over `hysa_bank_id` when both are
    set — an explicit fixed rate is a deliberate override, not just a
    fallback. `hysa_bank_id`/`benchmark_symbol_override` being unset falls
    back to `config.hysa_rates.default_bank_id`/`config.returns.benchmark_symbol`.
    `tax_regime` left unset falls back to `RESIDENT`, the fully taxed
    baseline, rather than assuming the more favorable nonresident-alien
    treatment on the user's behalf; `residency_status_change_date` left
    unset means `tax_regime` has applied to the whole account history.
    `marginal_ordinary_rate_pct`/`qualified_ltcg_rate_pct` left unset fall
    back to `config.tax.marginal_ordinary_rate`/`config.tax.qualified_ltcg_rate`.
    `w8ben_treaty_rate_pct` only means anything when `tax_regime` is `NRA`
    and `w8ben_claimed` is set — it does not change any historical figure
    (real withholding already happened at whatever rate the broker
    actually applied); it only feeds the forward-looking tax-owed estimate.
    """

    model_config = ConfigDict(frozen=True)

    target_allocation_pct: dict[str, float] = Field(default_factory=dict)
    hysa_bank_id: str | None = None
    hysa_fixed_rate_pct: float | None = None
    benchmark_symbol_override: str | None = None
    tax_enabled: bool = False
    tax_regime: TaxRegime | None = None
    residency_status_change_date: date | None = None
    w8ben_claimed: bool = False
    w8ben_treaty_rate_pct: float | None = None
    marginal_ordinary_rate_pct: float | None = None
    qualified_ltcg_rate_pct: float | None = None


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
    write_json_atomic(settings.model_dump(mode="json"), config.dashboard.settings_path)


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


@dataclass(frozen=True)
class OverviewCards:
    """Headline portfolio stats for the overview card row."""

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
    total_deposited_usd: float
    total_withdrawn_usd: float
    total_dividends_usd: float


def _raw_hysa_rate_lookup(config: AppConfig) -> Callable[[date], float]:
    """Build the published-rate HYSA lookup, before any after-tax adjustment.

    Priority: an explicit fixed-rate override, then the selected (or
    default) bank's real historical APY, falling back to
    `config.returns.hysa_annual_rate` for any day that bank has no
    published rate for yet (e.g. before its history starts).

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    settings = load_settings(config)
    if settings.hysa_fixed_rate_pct is not None:
        fixed_rate = settings.hysa_fixed_rate_pct / 100

        def fixed(_day: date) -> float:
            return fixed_rate

        return fixed

    bank_id = settings.hysa_bank_id or config.hysa_rates.default_bank_id
    history = hysa_rates_module.load_hysa_rates_cache(config)

    def rate(day: date) -> float:
        apy_pct = hysa_rates_module.rate_as_of(history, bank_id, day)
        return apy_pct / 100 if apy_pct is not None else config.returns.hysa_annual_rate

    return rate


def _hysa_rate_lookup(config: AppConfig) -> Callable[[date], float]:
    """Build the HYSA rate lookup every HYSA counterfactual on the dashboard shares.

    Because the overview's dollar-alpha card, the dollar chart, and the
    growth-of-$100 chart all source their HYSA leg from this one function,
    turning on `DashboardSettings.tax_enabled` here — wrapping the published
    rate through `taxes.after_tax_rate_lookup` — is enough to make every one
    of them switch from the pre-tax rate to an after-tax one at once,
    without each chart needing its own tax-awareness. Left off, this
    returns the published rate unchanged, exactly as before tax support
    existed.

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    settings = load_settings(config)
    raw_rate = _raw_hysa_rate_lookup(config)
    if not settings.tax_enabled:
        return raw_rate
    return after_tax_rate_lookup(
        raw_rate,
        resolved_marginal_ordinary_rate(config),
        resolved_tax_regime(config),
        settings.residency_status_change_date,
    )


def _hysa_rate_series(dates: pl.Series, config: AppConfig) -> pl.DataFrame:
    """Sample the resolved HYSA rate (as a percentage) over a set of dates.

    Lets the dollar and growth-of-100 charts show the actual rate in
    effect at each point, not just the dollar/index value it produced.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `hysa_rate_pct`.
    """
    rate_lookup = _hysa_rate_lookup(config)
    return _series_from_lookup(dates, lambda day: rate_lookup(day) * 100, "hysa_rate_pct")


def resolved_benchmark_symbol(config: AppConfig) -> str:
    """Resolve the benchmark symbol to use: the user's override if set, else `config.returns.benchmark_symbol`.

    Returns
    -------
    str
        The ticker symbol to benchmark against.
    """
    return load_settings(config).benchmark_symbol_override or config.returns.benchmark_symbol


def resolved_tax_regime(config: AppConfig) -> TaxRegime:
    """Resolve the tax regime to use: the user's selection, or the fully taxed default if never made.

    Returns
    -------
    TaxRegime
        `RESIDENT` unless the user has explicitly selected `NRA` — the
        dashboard never assumes the more favorable nonresident-alien
        treatment on the user's behalf.
    """
    return load_settings(config).tax_regime or "RESIDENT"


def resolved_marginal_ordinary_rate(config: AppConfig) -> float:
    """Resolve the ordinary-income tax rate to use: the user's override, or the code default.

    Returns
    -------
    float
        `config.tax.marginal_ordinary_rate` unless the user has entered their own rate.
    """
    override = load_settings(config).marginal_ordinary_rate_pct
    return override / 100 if override is not None else config.tax.marginal_ordinary_rate


def resolved_qualified_ltcg_rate(config: AppConfig) -> float:
    """Resolve the long-term-capital-gains/qualified-dividend rate to use: the user's override, or the code default.

    Returns
    -------
    float
        `config.tax.qualified_ltcg_rate` unless the user has entered their own rate.
    """
    override = load_settings(config).qualified_ltcg_rate_pct
    return override / 100 if override is not None else config.tax.qualified_ltcg_rate


def resolved_nra_dividend_tax_rate(config: AppConfig) -> float:
    """Resolve the flat rate a nonresident alien's dividends are taxed at.

    A tax treaty only lowers the rate below the default statutory
    withholding rate if it has actually been claimed (IRS Form W-8BEN) and
    a specific negotiated rate has been entered; claiming it without
    giving a rate is treated the same as not claiming it at all, since the
    statutory rate is the safe assumption absent a known number.

    Returns
    -------
    float
        The claimed treaty rate, or `config.tax.nra_statutory_dividend_withholding_rate`.
    """
    settings = load_settings(config)
    if settings.w8ben_claimed and settings.w8ben_treaty_rate_pct is not None:
        return settings.w8ben_treaty_rate_pct / 100
    return config.tax.nra_statutory_dividend_withholding_rate


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
    xirr_pct = xirr(dates, amounts, config) * 100
    is_provisional = (as_of - first_flow_date).days < config.returns.annualization_days

    daily_values = daily_portfolio_values(ledger, price_lookup, first_flow_date, as_of, config)
    nav = nav_series(daily_values, flows)
    twr = time_weighted_return(nav, start=first_flow_date, end=as_of, config=config)
    return xirr_pct, is_provisional, twr


def _gross_deposits_and_dividends(ledger: pl.DataFrame) -> tuple[float, float, float]:
    """Sum gross `DEPOSIT`, `WITHDRAWAL`, and `DIVIDEND` amounts, for the overview card's "money in" context.

    Deliberately gross, not netted against each other: shown side by side
    so `value = (deposited - withdrawn) + gain` visibly reconciles, rather
    than a lone "money in" figure that looks wrong once a withdrawal has
    happened (deposits alone won't explain the gap to `value`).

    Returns
    -------
    tuple[float, float, float]
        `(total_deposited, total_withdrawn, total_dividends)`.
    """
    total_deposited = float(ledger.filter(pl.col("event_type") == "DEPOSIT")["amount"].sum())
    total_withdrawn = float(ledger.filter(pl.col("event_type") == "WITHDRAWAL")["amount"].sum())
    total_dividends = float(ledger.filter(pl.col("event_type") == "DIVIDEND")["amount"].sum())
    return total_deposited, total_withdrawn, total_dividends


def overview_cards(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> OverviewCards:
    """Assemble the overview card row: value, gain split, XIRR, dollar alpha, TWR.

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

    hysa_value = (
        hysa_counterfactual_value(flows, as_of, _hysa_rate_lookup(config), config.returns.days_per_year)
        if not flows.is_empty()
        else 0.0
    )
    timing_gap_pct = (
        xirr_pct - twr.annualized_pct if xirr_pct is not None and twr and twr.annualized_pct is not None else None
    )
    gross = _gross_deposits_and_dividends(ledger)

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
        total_deposited_usd=gross[0],
        total_withdrawn_usd=gross[1],
        total_dividends_usd=gross[2],
    )


def reallocation_markers(ledger: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Find dates where a sell funded a same-day buy of a different symbol.

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
    date with an external flow and holds flat otherwise.

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
    """Build the three/four-line dollar chart series.

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
        `hysa_value_usd`, `benchmark_value_usd`, `hysa_rate_pct`.
    """
    raw_lookup = make_price_lookup(config)
    adjusted_lookup = make_price_lookup(config, adjusted=True)
    benchmark_symbol = resolved_benchmark_symbol(config)

    daily_values = daily_portfolio_values(ledger, raw_lookup, start, end, config)
    flows = collect_if_lazy(external_cashflows(ledger))
    contributions = _cumulative_contributions(flows, daily_values["date"])
    hysa_series = hysa_counterfactual_series(flows, end, _hysa_rate_lookup(config), config.returns.days_per_year)
    benchmark_series = benchmark_counterfactual_series(flows, end, lambda day: adjusted_lookup(benchmark_symbol, day))
    hysa_rate = _hysa_rate_series(daily_values["date"], config)

    return (
        daily_values
        .rename({"value": "portfolio_value_usd"})
        .join(contributions.rename({"value": "contributions_usd"}), on="date", how="left")
        .join(hysa_series.rename({"value": "hysa_value_usd"}), on="date", how="left")
        .join(benchmark_series.rename({"value": "benchmark_value_usd"}), on="date", how="left")
        .join(hysa_rate, on="date", how="left")
        .fill_null(0.0)
        .sort("date")
    )


def _series_from_lookup(dates: pl.Series, lookup: Callable[[date], float | None], value_column: str) -> pl.DataFrame:
    """Sample a date-keyed lookup over a set of dates, dropping dates it has no value for.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value_column`.
    """
    values = [lookup(day) for day in dates.to_list()]
    return pl.DataFrame({"date": dates, value_column: values}).drop_nulls(value_column)


def _cpi_series(dates: pl.Series, config: AppConfig) -> pl.DataFrame:
    """Look up the CPI index for a set of dates, dropping dates before the series starts.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value`.
    """
    history = cpi_module.load_cpi_cache(config)
    return _series_from_lookup(dates, lambda day: cpi_module.cpi_as_of(history, day), "value")


def growth_of_100_chart(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> pl.DataFrame:
    """Build the growth-of-$100 chart: your NAV plus every benchmark, indexed to a common start.

    Unlike the dollar chart's counterfactuals, these benchmark/HYSA series
    are NOT a replay of your contributions — they're pure indices (the
    benchmark's own adjusted price, a single $100 compounding at the HYSA
    rate) reindexed to 100. Replaying contributions here would answer "how
    much have I put in," not "how did the benchmark perform" — a later,
    much bigger deposit would balloon a contribution-replayed index even
    if the benchmark itself hadn't moved. Because everything is reindexed
    to 100 at its own start, no cashflow matching is needed here, unlike
    the dollar chart — this is the one chart where benchmarks overlay
    directly against your own performance.

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
        Columns `date`, `portfolio_index`, `benchmark_index`, `hysa_index`,
        `cpi_index`, `hysa_rate_pct`.
    """
    raw_lookup = make_price_lookup(config)
    adjusted_lookup = make_price_lookup(config, adjusted=True)
    benchmark_symbol = resolved_benchmark_symbol(config)

    daily_values = daily_portfolio_values(ledger, raw_lookup, start, end, config)
    flows = collect_if_lazy(external_cashflows(ledger))
    nav = cast("pl.DataFrame", nav_series(daily_values, flows))

    benchmark_prices = _series_from_lookup(
        daily_values["date"], lambda day: adjusted_lookup(benchmark_symbol, day), "close"
    )
    benchmark_index = cast("pl.DataFrame", growth_of_100(benchmark_prices, "close"))

    hysa_principal = pl.DataFrame({
        "event_datetime": [datetime.combine(start, datetime.min.time())],
        "amount": [-100.0],
    })
    hysa_series = hysa_counterfactual_series(
        hysa_principal, end, _hysa_rate_lookup(config), config.returns.days_per_year
    )
    hysa_rate = _hysa_rate_series(daily_values["date"], config)

    cpi_index = cast("pl.DataFrame", growth_of_100(_cpi_series(daily_values["date"], config), "value"))

    return (
        nav
        .select("date", portfolio_index="nav")
        .join(benchmark_index.select("date", benchmark_index="index"), on="date", how="left")
        .join(hysa_series.select("date", hysa_index="value"), on="date", how="left")
        .join(cpi_index.select("date", cpi_index="index"), on="date", how="left")
        .join(hysa_rate, on="date", how="left")
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
    """Split each month's value change into contributions and actual market gain.

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


def _symbol_and_cash_values(
    ledger: pl.DataFrame,
    price_lookup: Callable[[str, date], float | None],
    symbols: Sequence[str],
    cash_symbol: str,
    as_of: date,
    config: AppConfig,
) -> dict[str, float]:
    """Value every symbol's holding plus cash as of a date.

    Returns
    -------
    dict[str, float]
        `{symbol: value, ..., cash_symbol: cash_balance}`.

    Raises
    ------
    ValueError
        If a price is unavailable for any symbol still held.
    """
    result = replay_ledger(ledger.filter(pl.col("event_datetime").dt.date() <= as_of), config)
    values: dict[str, float] = dict.fromkeys(symbols, 0.0)
    values[cash_symbol] = result.cash_balance
    if not result.open_lots.is_empty():
        by_symbol = result.open_lots.group_by("symbol").agg(shares=pl.col("shares").sum())
        for symbol, shares in zip(by_symbol["symbol"].to_list(), by_symbol["shares"].to_list(), strict=True):
            price = price_lookup(symbol, as_of)
            if price is None:
                message = f"No price available for {symbol} on or before {as_of}."
                raise ValueError(message)
            values[symbol] = shares * price
    return values


def _symbol_trading_contributions(ledger: pl.DataFrame, month_start: date, month_end: date) -> dict[str, float]:
    """Net `BUY` minus `SELL` amount per symbol within a month — a symbol's analog of "money in".

    Returns
    -------
    dict[str, float]
        Symbol -> net trading contribution for the month.
    """
    in_month = pl.col("event_datetime").dt.date().is_between(month_start, month_end)
    month_trades = ledger.filter(in_month & pl.col("event_type").is_in(["BUY", "SELL"]))
    if month_trades.is_empty():
        return {}
    by_symbol = (
        month_trades
        .with_columns(signed=pl.when(pl.col("event_type") == "BUY").then(pl.col("amount")).otherwise(-pl.col("amount")))
        .group_by("symbol")
        .agg(contribution=pl.col("signed").sum())
    )
    return dict(zip(by_symbol["symbol"].to_list(), by_symbol["contribution"].to_list(), strict=True))


def monthly_pnl_by_symbol(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> pl.DataFrame:
    """Split each month's per-symbol value change into that symbol's trading and market gain.

    A symbol has no "external contribution" of its own — only the whole
    portfolio does (0.1) — so this defines a symbol's contribution as its
    net `BUY` minus `SELL` amount that month (money moved into or out of
    that position), with whatever's left over from external flows
    attributed to a `CASH` row. The two attributions reconcile exactly
    with the whole-portfolio `monthly_pnl`: contributions sum to net
    external flows, market gains sum to the same total market gain.

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
        Columns `month` (`"YYYY-MM"`), `symbol`, `contribution_usd`, `market_gain_usd`.
    """
    price_lookup = make_price_lookup(config)
    cash_symbol = config.ledger.cash_symbol
    symbols = sorted(set(ledger["symbol"].unique().to_list()) - {cash_symbol})
    flows = collect_if_lazy(external_cashflows(ledger))

    rows: list[dict[str, str | float]] = []
    for month_start, month_end in _month_boundaries(start, end):
        start_values = _symbol_and_cash_values(
            ledger, price_lookup, symbols, cash_symbol, month_start - timedelta(days=1), config
        )
        end_values = _symbol_and_cash_values(ledger, price_lookup, symbols, cash_symbol, month_end, config)
        trading = _symbol_trading_contributions(ledger, month_start, month_end)

        month_flows = flows.filter(pl.col("event_datetime").dt.date().is_between(month_start, month_end))
        net_external = -float(month_flows["amount"].sum()) if not month_flows.is_empty() else 0.0
        cash_contribution = net_external - sum(trading.values())

        month_label = month_start.strftime("%Y-%m")
        for symbol in symbols:
            contribution = trading.get(symbol, 0.0)
            gain = end_values[symbol] - start_values[symbol] - contribution
            rows.append({
                "month": month_label,
                "symbol": symbol,
                "contribution_usd": contribution,
                "market_gain_usd": gain,
            })
        cash_gain = end_values[cash_symbol] - start_values[cash_symbol] - cash_contribution
        rows.append({
            "month": month_label,
            "symbol": cash_symbol,
            "contribution_usd": cash_contribution,
            "market_gain_usd": cash_gain,
        })

    return pl.DataFrame(rows)


def allocation_view(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> pl.DataFrame:
    """Current-value allocation by symbol (including cash), against a user-set target.

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
    """Open lots (with returns), closed lots (with vs-HYSA alpha), and a per-symbol rollup."""

    open_lots: pl.DataFrame
    closed_lots: pl.DataFrame
    symbol_rollup: pl.DataFrame


def _closed_lots_with_hysa_alpha(closed_lots: pl.DataFrame, config: AppConfig) -> pl.DataFrame:
    """Add a total-return and vs-HYSA alpha to every closed lot, over its actual holding window.

    A closed lot's window is finished, so this alpha is a legitimate,
    non-provisional number (unlike a live position's annualized return,
    which stays hidden until it's been held long enough — see
    `metrics.lot_returns`).

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
    """Assemble the trade-level table: open lots, closed lots, per-symbol rollup.

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
    rollup_rows = [asdict(symbol_metrics(ledger, result, symbol, price_lookup, as_of, config)) for symbol in symbols]
    symbol_rollup = pl.DataFrame(rollup_rows) if rollup_rows else pl.DataFrame()

    return LotsTable(open_lots=open_lots, closed_lots=closed_lots, symbol_rollup=symbol_rollup)


def risk_stat(ledger: pl.DataFrame, config: AppConfig, start: date, end: date) -> float:
    """Largest peak-to-trough decline in the NAV series, as a percentage.

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
    """Last cached price date per symbol, for the data-quality panel.

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


def _after_tax_dollar_alpha_vs_hysa(
    ledger: pl.DataFrame,
    config: AppConfig,
    as_of: date,
    regime: TaxRegime,
    status_change_date: date | None,
    marginal_ordinary_rate: float,
) -> float:
    """Dollar alpha vs. HYSA, using the after-tax rate in place of the raw published rate.

    Mirrors `overview_cards`'s pre-tax figure exactly, substituting an
    `after_tax_rate_lookup`-wrapped rate for the plain one — everything
    else about the comparison (replaying the same deposit/withdrawal
    history against a virtual savings account) is unchanged.

    Returns
    -------
    float
        Portfolio value minus the after-tax HYSA counterfactual value, as of `as_of`.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    flows = collect_if_lazy(external_cashflows(ledger))
    if flows.is_empty():
        return value

    after_tax_lookup = after_tax_rate_lookup(
        _raw_hysa_rate_lookup(config), marginal_ordinary_rate, regime, status_change_date
    )
    hysa_value = hysa_counterfactual_value(flows, as_of, after_tax_lookup, config.returns.days_per_year)
    return value - hysa_value


@dataclass(frozen=True)
class LiquidationEstimate:
    """What a full sale of every open lot, today, would leave you with, and the arithmetic behind that number.

    `pretax_value_usd` is the portfolio's current value; the gain buckets
    and `capital_gains_tax_usd` show how much of that value a hypothetical
    sale right now would owe in tax (see `taxes.liquidation_gain_buckets`);
    `after_tax_value_usd = pretax_value_usd - capital_gains_tax_usd` is what
    would actually be left over.
    """

    pretax_value_usd: float
    long_term_gain_usd: float
    short_term_gain_usd: float
    capital_gains_tax_usd: float
    after_tax_value_usd: float


def _liquidation_estimate(
    ledger: pl.DataFrame,
    config: AppConfig,
    as_of: date,
    regime: TaxRegime,
    marginal_ordinary_rate: float,
    qualified_ltcg_rate: float,
) -> LiquidationEstimate:
    """Estimate what selling every open lot right now, and paying the resulting tax, would leave you with.

    Every open lot is previewed as if sold `as_of` (see `taxes.preview_sale`)
    and taxed as `taxes.liquidation_tax_usd` describes — a resident alien's
    net gain in each holding-period bucket, a nonresident alien's nothing.
    This is a snapshot, not a projection: it says nothing about what
    selling gradually, or on a different future date, would owe.

    Returns
    -------
    LiquidationEstimate
        The pre-tax value, the gain buckets and tax a sale would trigger, and what would be left over.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    if result.open_lots.is_empty():
        return LiquidationEstimate(value, 0.0, 0.0, 0.0, value)

    previews = preview_sale(result.open_lots, ledger, price_lookup, as_of, config)
    long_term_gain, short_term_gain = liquidation_gain_buckets(previews)
    tax = liquidation_tax_usd(previews, regime, marginal_ordinary_rate, qualified_ltcg_rate)
    return LiquidationEstimate(value, long_term_gain, short_term_gain, tax, value - tax)


@dataclass(frozen=True)
class TaxSummary:
    """The full tax view: realized gains/dividends by year, estimated tax owed, wash sales, and sale previews."""

    annual: pl.DataFrame
    tax_owed: pl.DataFrame
    wash_sales: pl.DataFrame
    sale_previews: pl.DataFrame
    after_tax_dollar_alpha_vs_hysa_usd: float
    liquidation_pretax_value_usd: float
    liquidation_long_term_gain_usd: float
    liquidation_short_term_gain_usd: float
    liquidation_capital_gains_tax_usd: float
    liquidation_value_usd: float


def tax_summary(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> TaxSummary:
    """Assemble the full tax view: the annual report, estimated tax owed, flagged wash sales, and sale previews.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    as_of
        The date to preview open-lot sales, and value the after-tax comparisons, as of.

    Returns
    -------
    TaxSummary
        The full tax view, ready to serialize.
    """
    settings = load_settings(config)
    regime = resolved_tax_regime(config)
    status_change_date = settings.residency_status_change_date
    marginal_ordinary_rate = resolved_marginal_ordinary_rate(config)
    qualified_ltcg_rate = resolved_qualified_ltcg_rate(config)
    result = replay_ledger(ledger, config)

    annual = annual_tax_report(result.closed_lots, ledger, config, regime, status_change_date)
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate, qualified_ltcg_rate, resolved_nra_dividend_tax_rate(config)
    )
    wash_sales = cast("pl.DataFrame", flag_wash_sales(result.closed_lots, ledger, config)).filter(
        pl.col("wash_sale_flag")
    )
    previews = preview_sale(result.open_lots, ledger, make_price_lookup(config), as_of, config)
    after_tax_alpha = _after_tax_dollar_alpha_vs_hysa(
        ledger, config, as_of, regime, status_change_date, marginal_ordinary_rate
    )
    liquidation = _liquidation_estimate(ledger, config, as_of, regime, marginal_ordinary_rate, qualified_ltcg_rate)

    return TaxSummary(
        annual=annual,
        tax_owed=owed,
        wash_sales=wash_sales,
        sale_previews=previews,
        after_tax_dollar_alpha_vs_hysa_usd=after_tax_alpha,
        liquidation_pretax_value_usd=liquidation.pretax_value_usd,
        liquidation_long_term_gain_usd=liquidation.long_term_gain_usd,
        liquidation_short_term_gain_usd=liquidation.short_term_gain_usd,
        liquidation_capital_gains_tax_usd=liquidation.capital_gains_tax_usd,
        liquidation_value_usd=liquidation.after_tax_value_usd,
    )
