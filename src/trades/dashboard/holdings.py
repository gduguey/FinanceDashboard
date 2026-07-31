"""Holdings tables, allocation view, and data-quality checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.dashboard.settings import hysa_rate_lookup
from trades.dashboard.valuation import make_price_lookup
from trades.ledger.metrics import lot_returns, symbol_metrics
from trades.ledger.replay import replay_ledger
from trades.market_data import prices

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from trades.config import AppConfig
    from trades.dashboard.settings import DashboardSettings


@dataclass(frozen=True)
class LotsTable:
    """Open lots (with returns), closed lots (with their excess return over a HYSA), and a per-symbol rollup."""

    open_lots: pl.DataFrame
    closed_lots: pl.DataFrame
    symbol_rollup: pl.DataFrame


def _hysa_growth_index(
    start: date, end: date, rate_lookup: Callable[[date], float], days_per_year: int
) -> pl.DataFrame:
    """Compound one unit of money in a HYSA day by day, and keep the running factor for every date.

    The same walk `ledger.counterfactuals.hysa_counterfactual_series` does
    for the overview's own HYSA leg, and deliberately the same convention:
    a day's factor is recorded before that day's interest is applied, so
    the growth over `[opened, closed)` is `factor[closed] / factor[opened]`
    — no interest on the day the lot is sold.

    Computed once for the whole span rather than once per lot: the rate
    lookup is a filter over the bank's published history, and doing it per
    lot would repeat that for every overlapping holding window.

    Parameters
    ----------
    start, end
        The span to cover, inclusive.
    rate_lookup
        Looks up the annual HYSA rate as of a given date (see
        `dashboard.settings.hysa_rate_lookup`).
    days_per_year
        Day-count basis for converting the annual rate to a daily one.

    Returns
    -------
    polars.DataFrame
        Columns `date`, `growth_factor`.
    """
    dates: list[date] = []
    factors: list[float] = []
    factor = 1.0
    day = start
    while day <= end:
        dates.append(day)
        factors.append(factor)
        factor *= 1 + rate_lookup(day) / days_per_year
        day += timedelta(days=1)
    return pl.DataFrame(
        {"date": dates, "growth_factor": factors}, schema={"date": pl.Date, "growth_factor": pl.Float64}
    )


def _closed_lots_with_hysa_comparison(
    closed_lots: pl.DataFrame, config: AppConfig, settings: DashboardSettings
) -> pl.DataFrame:
    """Add a total return, and its excess over a HYSA, to every closed lot over that lot's actual holding window.

    A closed lot's window is finished, so this is a legitimate,
    non-provisional number (unlike a live position's annualized return,
    which stays hidden until it's been held long enough — see
    `metrics.lot_returns`).

    It is not alpha, and is no longer labelled as one: alpha is a
    risk-adjusted excess return over a market benchmark, and this is the
    plain arithmetic difference between what the lot returned and what the
    same money would have earned sitting in savings.

    The HYSA leg comes from `settings.hysa_rate_lookup` — the user's real
    selected bank's published APY, their fixed-rate override if they set
    one, and after tax when they have tax enabled — which is the same rate
    the overview's own HYSA counterfactual uses. It used to be
    `config.returns.hysa_annual_rate`, a flat `0.04`, so the two figures on
    screen were measured against two different rates under one label. That
    constant survives as `raw_hysa_rate_lookup`'s fallback for days the
    chosen bank published nothing, which is the only thing it was ever
    needed for.

    Returns
    -------
    polars.DataFrame
        `closed_lots` plus `days_held`, `total_return_pct`, `excess_return_vs_hysa_pct`.
    """
    if closed_lots.is_empty():
        return closed_lots.with_columns(
            days_held=pl.lit(None, dtype=pl.Int64),
            total_return_pct=pl.lit(None, dtype=pl.Float64),
            excess_return_vs_hysa_pct=pl.lit(None, dtype=pl.Float64),
        )
    growth = _hysa_growth_index(
        cast("date", closed_lots["opened_at"].dt.date().min()),
        cast("date", closed_lots["closed_at"].dt.date().max()),
        hysa_rate_lookup(config, settings),
        config.returns.days_per_year,
    )
    return (
        closed_lots
        .with_columns(
            days_held=(pl.col("closed_at").dt.date() - pl.col("opened_at").dt.date()).dt.total_days(),
            total_return_pct=(
                (pl.col("realized_gain") + pl.col("dividends_received")) / (pl.col("shares") * pl.col("cost_per_share"))
            )
            * 100,
            opened_on=pl.col("opened_at").dt.date(),
            closed_on=pl.col("closed_at").dt.date(),
        )
        .join(growth.rename({"date": "opened_on", "growth_factor": "growth_at_open"}), on="opened_on", how="left")
        .join(growth.rename({"date": "closed_on", "growth_factor": "growth_at_close"}), on="closed_on", how="left")
        .with_columns(
            excess_return_vs_hysa_pct=pl.col("total_return_pct")
            - ((pl.col("growth_at_close") / pl.col("growth_at_open") - 1) * 100)
        )
        .drop("opened_on", "closed_on", "growth_at_open", "growth_at_close")
    )


def lots_table(ledger: pl.DataFrame, config: AppConfig, settings: DashboardSettings, as_of: date) -> LotsTable:
    """Assemble the trade-level table: open lots, closed lots, per-symbol rollup.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.
    as_of
        The date to price open lots as of.

    Returns
    -------
    LotsTable
        Open lots, closed lots, and the per-symbol rollup.
    """
    price_lookup = make_price_lookup(config)
    net_dividends = settings.tax_enabled
    result = replay_ledger(ledger, config, net_dividends=net_dividends)

    open_lots = (
        cast("pl.DataFrame", lot_returns(result.open_lots, price_lookup, as_of, config))
        if not result.open_lots.is_empty()
        else result.open_lots
    )
    closed_lots = _closed_lots_with_hysa_comparison(result.closed_lots, config, settings)

    symbols = sorted({*result.open_lots["symbol"].to_list(), *result.closed_lots["symbol"].to_list()})
    rollup_rows = [
        asdict(symbol_metrics(ledger, result, symbol, price_lookup, as_of, config, net_dividends=net_dividends))
        for symbol in symbols
    ]
    symbol_rollup = pl.DataFrame(rollup_rows) if rollup_rows else pl.DataFrame()

    return LotsTable(open_lots=open_lots, closed_lots=closed_lots, symbol_rollup=symbol_rollup)


def allocation_view(ledger: pl.DataFrame, config: AppConfig, settings: DashboardSettings, as_of: date) -> pl.DataFrame:
    """Current-value allocation by symbol (including cash), against a user-set target.

    Sliced by current value, not invested dollars — invested-dollar slices
    can't show drift from a target allocation.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.
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
