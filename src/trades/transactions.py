"""Loading, enriching, and summarizing the raw trade blotter.

Pipeline: `load_raw_trades` -> `enrich_trades` -> `aggregate_same_day_trades`.
Everything below the pipeline (schedules, pies, timelines) reads from the
enriched or aggregated DataFrame; none of it talks to the network or to disk.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from trades.config import AggregationConfig
from trades.models import RawTrade


def standardize_ibkr_trades(ledger: pd.DataFrame) -> pd.DataFrame:
    """Derive the older, narrower "invested schedule" trade shape from the
    ledger: `BUY` events on a real symbol (excluding `CASH`) become one row
    each, `usd_spent` = the event's principal `amount` (commission is its
    own `FEE` event, deliberately excluded here — see docs/architecture.md,
    "Canonical trade schema"). `event_datetime` is truncated to a date, the
    grain `transactions.py`'s aggregation and rollups are built around.
    """
    buys = ledger[(ledger["event_type"] == "BUY") & (ledger["symbol"] != _CASH_SYMBOL)]
    if buys.empty:
        return pd.DataFrame(columns=list(RawTrade.model_fields)).astype(
            {"trade_date": "datetime64[ns]", "shares": "float64", "usd_spent": "float64"}
        )

    standardized = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(buys["event_datetime"]).dt.date,
            "symbol": buys["symbol"],
            "shares": buys["shares"],
            "usd_spent": buys["amount"],
        }
    )
    trades = [RawTrade.model_validate(row.to_dict()) for _, row in standardized.iterrows()]
    df = pd.DataFrame([t.model_dump() for t in trades])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def load_raw_trades(csv_path: Path) -> pd.DataFrame:
    """Read the broker CSV, validating every row through `RawTrade`.

    Returns columns: trade_date (datetime64), symbol, shares, usd_spent.
    """
    raw = pd.read_csv(csv_path, dtype=str)
    trades = [RawTrade.model_validate(row.to_dict()) for _, row in raw.iterrows()]
    df = pd.DataFrame([t.model_dump() for t in trades])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def enrich_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Add the `usd_per_share` column (the reason this pipeline stage exists)."""
    out = df.copy()
    out["usd_per_share"] = out["usd_spent"] / out["shares"]
    return out


def aggregate_same_day_trades(df: pd.DataFrame, config: AggregationConfig) -> pd.DataFrame:
    """Merge same-day, same-symbol fills executed at (nearly) the same price.

    Within each (trade_date, symbol) group, rows are sorted by `usd_per_share`
    and chained into a cluster while each next price sits within
    `config.same_day_price_tolerance` (relative) of the previous one. Each
    cluster becomes one row: shares and usd_spent summed, usd_per_share
    recomputed from those sums.
    """
    tolerance = config.same_day_price_tolerance
    ordered = df.sort_values(["trade_date", "symbol", "usd_per_share"])
    records: list[dict] = []
    for (trade_date, symbol), group in ordered.groupby(["trade_date", "symbol"], sort=False):
        prices = group["usd_per_share"].to_numpy()
        rows = group.to_dict("records")
        cluster_start = 0
        for i in range(1, len(rows) + 1):
            at_boundary = (
                i == len(rows) or abs(prices[i] - prices[i - 1]) > tolerance * prices[i - 1]
            )
            if not at_boundary:
                continue
            cluster = rows[cluster_start:i]
            shares = sum(r["shares"] for r in cluster)
            usd_spent = sum(r["usd_spent"] for r in cluster)
            records.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "shares": shares,
                    "usd_spent": usd_spent,
                    "usd_per_share": usd_spent / shares,
                    "n_trades": len(cluster),
                }
            )
            cluster_start = i
    return (
        pd.DataFrame.from_records(records)
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )


def total_invested_by_symbol(df: pd.DataFrame) -> pd.Series:
    return df.groupby("symbol")["usd_spent"].sum().sort_values(ascending=False)


def monthly_invested(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot: one row per calendar month, one column per symbol, plus Total."""
    working = df.copy()
    working["month"] = working["trade_date"].dt.to_period("M").astype(str)
    pivot = working.pivot_table(
        index="month", columns="symbol", values="usd_spent", aggfunc="sum", fill_value=0.0
    )
    pivot["Total"] = pivot.sum(axis=1)
    return pivot.sort_index()


def daily_investment_timeline(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day a trade happened: amount invested, running total, and
    the gap (in days) since the previous investment day."""
    daily = df.groupby("trade_date")["usd_spent"].sum().sort_index().reset_index()
    daily["cumulative_usd_spent"] = daily["usd_spent"].cumsum()
    daily["days_since_previous_investment"] = daily["trade_date"].diff().dt.days
    return daily


def pie_breakdown(df: pd.DataFrame, symbol: str | None = None) -> pd.Series:
    """Invested USD by symbol (symbol=None) or, for one symbol, by trade date."""
    if symbol is None:
        return df.groupby("symbol")["usd_spent"].sum().sort_values(ascending=False)
    subset = df[df["symbol"] == symbol]
    return subset.groupby(subset["trade_date"].dt.date)["usd_spent"].sum().sort_index()


def pie_chart_options(df: pd.DataFrame) -> dict[str, pd.Series]:
    """All the slices a pie-chart menu should offer: portfolio-by-symbol plus
    one by-date breakdown per symbol."""
    options = {"Whole portfolio — by symbol": pie_breakdown(df, symbol=None)}
    for symbol in sorted(df["symbol"].unique()):
        options[f"{symbol} — by date"] = pie_breakdown(df, symbol=symbol)
    return options
