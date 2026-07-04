"""Loading, enriching, and summarizing the "invested schedule" trade shape.

Pipeline: `standardize_ibkr_trades`/`load_raw_trades` -> `enrich_trades` ->
`aggregate_same_day_trades`. Everything below the pipeline (schedules,
pies, timelines) reads from the enriched or aggregated frame; none of it
talks to the network or to disk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import polars as pl

from trades.models import RawTrade
from trades.utils.frames import collect_if_lazy, preserve_frame_type

if TYPE_CHECKING:
    from pathlib import Path

    from trades.config import AppConfig


def standardize_ibkr_trades(ledger: pl.DataFrame | pl.LazyFrame, config: AppConfig) -> pl.DataFrame | pl.LazyFrame:
    """Derive the "invested schedule" trade shape from the ledger.

    `BUY` events on a real symbol (excluding `config.ledger.cash_symbol`)
    become one row each; `usd_spent` is the event's principal `amount`
    (commission is its own `FEE` event, deliberately excluded here — see
    docs/architecture.md, "Canonical trade schema"). `event_datetime` is
    truncated to a date, the grain this module's aggregation and rollups
    are built around.

    Parameters
    ----------
    ledger
        The event ledger (see `brokers/ibkr/main.py:load_ledger`). Row
        iteration requires the data to be materialized, so a `LazyFrame`
        is collected immediately.
    config
        Application configuration; `config.ledger.cash_symbol` is read.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Columns `trade_date`, `symbol`, `shares`, `usd_spent`, validated
        through `RawTrade`, sorted by `trade_date`/`symbol`. Same type as
        `ledger`.
    """
    was_eager = isinstance(ledger, pl.DataFrame)
    buys = collect_if_lazy(
        ledger.filter((pl.col("event_type") == "BUY") & (pl.col("symbol") != config.ledger.cash_symbol)).select(
            trade_date=pl.col("event_datetime").dt.date(),
            symbol=pl.col("symbol"),
            shares=pl.col("shares"),
            usd_spent=pl.col("amount"),
        )
    )
    trades = [RawTrade.model_validate(row) for row in buys.iter_rows(named=True)]
    standardized = pl.DataFrame([trade.model_dump() for trade in trades], schema=RawTrade.polars_schema)
    result = standardized.lazy().sort("trade_date", "symbol")
    return result.collect() if was_eager else result


def load_raw_trades(csv_path: Path) -> pl.DataFrame:
    """Read a broker CSV export, validating every row through `RawTrade`.

    Parameters
    ----------
    csv_path
        Path to the CSV, with columns `Date`, `Symbol`, `Shares`, `USD Spent`.

    Returns
    -------
    polars.DataFrame
        Columns `trade_date`, `symbol`, `shares`, `usd_spent`, sorted by
        `trade_date`/`symbol`.
    """
    raw = pl.read_csv(csv_path, infer_schema=False)
    trades = [RawTrade.model_validate(row) for row in raw.iter_rows(named=True)]
    standardized = pl.DataFrame([trade.model_dump() for trade in trades], schema=RawTrade.polars_schema)
    return standardized.sort("trade_date", "symbol")


def enrich_trades(df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Add the `usd_per_share` column (the reason this pipeline stage exists).

    Parameters
    ----------
    df
        Trade rows with `usd_spent` and `shares` columns.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `df` plus `usd_per_share`. Same type as `df`.
    """
    return df.with_columns(usd_per_share=pl.col("usd_spent") / pl.col("shares"))


def aggregate_same_day_trades(df: pl.DataFrame | pl.LazyFrame, config: AppConfig) -> pl.DataFrame | pl.LazyFrame:
    """Merge same-day, same-symbol fills executed at (nearly) the same price.

    Within each (`trade_date`, `symbol`) group, rows are sorted by
    `usd_per_share` and chained into a cluster while each next price sits
    within `config.aggregation.same_day_price_tolerance` (relative) of the
    previous one. Each cluster becomes one row: shares and usd_spent
    summed, usd_per_share recomputed from those sums.

    Parameters
    ----------
    df
        Enriched trade rows (see `enrich_trades`).
    config
        Application configuration; `config.aggregation.same_day_price_tolerance` is read.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        One row per cluster, plus `n_trades`. Same type as `df`.
    """
    tolerance = config.aggregation.same_day_price_tolerance
    ordered = df.lazy().sort("trade_date", "symbol", "usd_per_share")
    with_prev = ordered.with_columns(prev_price=pl.col("usd_per_share").shift(1).over("trade_date", "symbol"))
    is_new_cluster = pl.col("prev_price").is_null() | (
        (pl.col("usd_per_share") - pl.col("prev_price")).abs() > tolerance * pl.col("prev_price")
    )
    clustered = with_prev.with_columns(cluster_id=is_new_cluster.cum_sum().over("trade_date", "symbol"))
    result = (
        clustered
        .group_by("trade_date", "symbol", "cluster_id", maintain_order=True)
        .agg(shares=pl.col("shares").sum(), usd_spent=pl.col("usd_spent").sum(), n_trades=pl.len())
        .with_columns(usd_per_share=pl.col("usd_spent") / pl.col("shares"))
        .drop("cluster_id")
        .sort("trade_date", "symbol")
    )
    return preserve_frame_type(result, df)


def monthly_invested(df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Pivot: one row per calendar month, one column per symbol, plus Total.

    Parameters
    ----------
    df
        Trade rows with `trade_date`, `symbol`, `usd_spent` columns.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        One row per month, sorted ascending. Same type as `df`.
    """
    was_eager = isinstance(df, pl.DataFrame)
    collected = collect_if_lazy(df.with_columns(month=pl.col("trade_date").dt.strftime("%Y-%m")))
    pivot = (
        collected
        .pivot(on="symbol", index="month", values="usd_spent", aggregate_function="sum")
        .fill_null(0.0)
        .with_columns(Total=pl.sum_horizontal(pl.exclude("month")))
        .sort("month")
    )
    return pivot if was_eager else pivot.lazy()


def daily_investment_timeline(df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """One row per day a trade happened.

    Reports the amount invested, the running total, and the gap (in days)
    since the previous investment day.

    Parameters
    ----------
    df
        Trade rows with `trade_date`, `usd_spent` columns.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Sorted by `trade_date`. Same type as `df`.
    """
    result = (
        df
        .lazy()
        .group_by("trade_date")
        .agg(usd_spent=pl.col("usd_spent").sum())
        .sort("trade_date")
        .with_columns(
            cumulative_usd_spent=pl.col("usd_spent").cum_sum(),
            days_since_previous_investment=pl.col("trade_date").diff().dt.total_days(),
        )
    )
    return preserve_frame_type(result, df)


def pie_breakdown(df: pl.DataFrame | pl.LazyFrame, symbol: str | None = None) -> pl.DataFrame | pl.LazyFrame:
    """Invested USD by symbol (`symbol=None`) or, for one symbol, by trade date.

    Parameters
    ----------
    df
        Trade rows with `trade_date`, `symbol`, `usd_spent` columns.
    symbol
        If given, restrict to this symbol and break down by trade date
        instead of by symbol.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        Sorted descending by `usd_spent` (whole-portfolio case) or
        ascending by `trade_date` (single-symbol case). Same type as `df`.
    """
    lazy_df = df.lazy()
    if symbol is None:
        result = lazy_df.group_by("symbol").agg(usd_spent=pl.col("usd_spent").sum()).sort("usd_spent", descending=True)
    else:
        result = (
            lazy_df
            .filter(pl.col("symbol") == symbol)
            .group_by("trade_date")
            .agg(usd_spent=pl.col("usd_spent").sum())
            .sort("trade_date")
        )
    return preserve_frame_type(result, df)


def pie_chart_options(df: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """All the slices a pie-chart menu should offer.

    Portfolio-by-symbol plus one by-date breakdown per symbol.

    Parameters
    ----------
    df
        Trade rows with `trade_date`, `symbol`, `usd_spent` columns.

    Returns
    -------
    dict[str, polars.DataFrame]
        One entry for the whole portfolio, plus one per symbol.
    """
    symbols = sorted(df["symbol"].unique().to_list())
    return {"Whole portfolio — by symbol": cast("pl.DataFrame", pie_breakdown(df, symbol=None))} | {
        f"{symbol} — by date": cast("pl.DataFrame", pie_breakdown(df, symbol=symbol)) for symbol in symbols
    }


def total_invested_by_symbol(df: pl.LazyFrame | pl.DataFrame) -> pl.Series:
    """Total USD invested per symbol, sorted descending.

    Parameters
    ----------
    df
        Trade rows with `symbol`, `usd_spent` columns.

    Returns
    -------
    polars.Series
        Total USD invested per symbol, sorted descending.
    """
    df = collect_if_lazy(df)
    return (
        df.group_by("symbol")
        .agg(pl.col("usd_spent").sum().alias("total_invested"))
        .sort("total_invested", descending=True)
        .get_column("total_invested")
    )