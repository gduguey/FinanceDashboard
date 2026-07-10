"""Holdings tables, allocation view, and data-quality checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.dashboard.settings import load_settings
from trades.dashboard.valuation import make_price_lookup
from trades.ledger.metrics import lot_returns, symbol_metrics
from trades.ledger.replay import replay_ledger
from trades.market_data import prices

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from trades.config import AppConfig


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
    net_dividends = load_settings(config).tax_enabled
    result = replay_ledger(ledger, config, net_dividends=net_dividends)

    open_lots = (
        cast("pl.DataFrame", lot_returns(result.open_lots, price_lookup, as_of, config))
        if not result.open_lots.is_empty()
        else result.open_lots
    )
    closed_lots = _closed_lots_with_hysa_alpha(result.closed_lots, config)

    symbols = sorted({*result.open_lots["symbol"].to_list(), *result.closed_lots["symbol"].to_list()})
    rollup_rows = [
        asdict(symbol_metrics(ledger, result, symbol, price_lookup, as_of, config, net_dividends=net_dividends))
        for symbol in symbols
    ]
    symbol_rollup = pl.DataFrame(rollup_rows) if rollup_rows else pl.DataFrame()

    return LotsTable(open_lots=open_lots, closed_lots=closed_lots, symbol_rollup=symbol_rollup)


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
