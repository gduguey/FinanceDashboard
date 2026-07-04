"""Chart and time-series builders for the dashboard."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.dashboard.settings import hysa_rate_lookup, resolved_benchmark_symbol
from trades.dashboard.valuation import daily_portfolio_values, make_price_lookup
from trades.ledger.counterfactuals import benchmark_counterfactual_series, hysa_counterfactual_series
from trades.ledger.metrics import max_drawdown
from trades.ledger.nav import growth_of_100, nav_series, period_pnl
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger
from trades.market_data import cpi as cpi_module
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from trades.config import AppConfig


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


def _series_from_lookup(dates: pl.Series, lookup: Callable[[date], float | None], value_column: str) -> pl.DataFrame:
    """Sample a date-keyed lookup over a set of dates, dropping dates it has no value for.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value_column`.
    """
    values = [lookup(day) for day in dates.to_list()]
    return pl.DataFrame({"date": dates, value_column: values}).drop_nulls(value_column)


def _hysa_rate_series(dates: pl.Series, config: AppConfig) -> pl.DataFrame:
    """Sample the resolved HYSA rate (as a percentage) over a set of dates.

    Lets the dollar and growth-of-100 charts show the actual rate in
    effect at each point, not just the dollar/index value it produced.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `hysa_rate_pct`.
    """
    rate_lookup = hysa_rate_lookup(config)
    return _series_from_lookup(dates, lambda day: rate_lookup(day) * 100, "hysa_rate_pct")


def _cpi_series(dates: pl.Series, config: AppConfig) -> pl.DataFrame:
    """Look up the CPI index for a set of dates, dropping dates before the series starts.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `value`.
    """
    history = cpi_module.load_cpi_cache(config)
    return _series_from_lookup(dates, lambda day: cpi_module.cpi_as_of(history, day), "value")


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
    hysa_series = hysa_counterfactual_series(flows, end, hysa_rate_lookup(config), config.returns.days_per_year)
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
        hysa_principal, end, hysa_rate_lookup(config), config.returns.days_per_year
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
        The first boundary's start is `start`, not snapped to the 1st of the month.
    """
    boundaries: list[tuple[date, date]] = []
    current = start
    while current <= end:
        # Month ends on the last day of the current month, clipped to `end`
        last_day_of_month = monthrange(current.year, current.month)[1]
        month_end = min(date(current.year, current.month, last_day_of_month), end)
        boundaries.append((current, month_end))
        # Next month starts the day after this month ends
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
    portfolio does — so this defines a symbol's contribution as its
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
