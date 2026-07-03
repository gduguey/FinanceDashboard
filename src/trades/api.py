"""JSON-over-HTTP view of the same data `visualization.py` renders to Plotly.

Every endpoint below just calls existing `transactions`/`returns`/`prices`/
`brokers.ibkr` functions and serializes the result — no aggregation or
fetching happens in this module, matching the rule `visualization.py`
already follows. See docs/architecture.md ("this split is what would let a
future React/API layer reuse the exact same logic modules behind HTTP
endpoints") and NEXT_STEPS.md #4.

GET endpoints only ever read what's already cached on disk — they never
make a network call. `POST /sync` is the one endpoint allowed to touch the
network (an IBKR pull plus a price-cache refresh for every known symbol);
that's what makes the frontend's "Sync" button a real, explicit action
instead of something that silently happens on every page load.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import FastAPI, HTTPException

from trades import prices, returns, transactions
from trades.brokers.ibkr import api, main, preprocessing
from trades.config import (
    AggregationConfig,
    IbkrFlexApiConfig,
    IbkrFlexCredentials,
    PriceApiConfig,
    ReturnsConfig,
)

aggregation_config = AggregationConfig()
price_api_config = PriceApiConfig()
returns_config = ReturnsConfig()
ibkr_config = IbkrFlexApiConfig()

app = FastAPI(title="Investments API")


def _records(df: pd.DataFrame) -> list[dict]:
    """`df.to_dict(orient="records")` with every date/Timestamp column
    rendered as an ISO string, so the result is directly JSON-serializable."""
    out = df.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
    return out.to_dict(orient="records")


def _load_trades() -> pd.DataFrame:
    """Load the ledger -> standardize to the older trade schema -> enrich ->
    aggregate, read-only (no network)."""
    ledger = main.load_ledger(ibkr_config)
    if ledger.empty:
        raise HTTPException(
            status_code=404, detail="No ledger cached yet. Hit Sync to pull it from IBKR."
        )
    standardized = transactions.standardize_ibkr_trades(ledger)
    enriched = transactions.enrich_trades(standardized)
    return transactions.aggregate_same_day_trades(enriched, aggregation_config)


def _price_lookup(symbol: str, as_of: date) -> float | None:
    history = prices.load_price_cache(symbol, price_api_config)
    return prices.price_as_of(history, as_of)


def _build_returns(trades: pd.DataFrame, as_of: date) -> pd.DataFrame:
    try:
        return returns.build_returns_table(
            trades, _price_lookup, as_of=as_of, config=returns_config
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/summary")
def get_summary() -> dict:
    trades = _load_trades()
    returns_df = _build_returns(trades, date.today())

    total_invested = float(trades["usd_spent"].sum())
    current_value = float((returns_df["shares"] * returns_df["current_price"]).sum())
    total_gain_usd = current_value - total_invested

    last_synced = api.last_synced_at(ibkr_config)
    last_synced = last_synced.isoformat() if last_synced else None

    return {
        "as_of_date": date.today().isoformat(),
        "total_invested_usd": total_invested,
        "current_value_usd": current_value,
        "total_gain_usd": total_gain_usd,
        "total_gain_pct": (total_gain_usd / total_invested * 100) if total_invested else None,
        "portfolio_alpha_pct": returns.portfolio_alpha_pct(returns_df),
        "hysa_annual_rate": returns_config.hysa_annual_rate,
        "symbol_count": int(trades["symbol"].nunique()),
        "last_synced_at": last_synced,
    }


@app.get("/api/trades")
def get_trades() -> list[dict]:
    return _records(_load_trades())


@app.get("/api/schedule/monthly")
def get_monthly_invested() -> list[dict]:
    monthly = transactions.monthly_invested(_load_trades()).reset_index()
    return _records(monthly)


@app.get("/api/schedule/daily")
def get_daily_timeline() -> list[dict]:
    return _records(transactions.daily_investment_timeline(_load_trades()))


@app.get("/api/schedule/pie")
def get_pie_breakdown() -> dict[str, list[dict]]:
    options = transactions.pie_chart_options(_load_trades())
    return {
        label: [{"name": str(name), "value": float(value)} for name, value in series.items()]
        for label, series in options.items()
    }


@app.get("/api/returns")
def get_returns(as_of: date | None = None) -> list[dict]:
    trades = _load_trades()
    returns_df = _build_returns(trades, as_of or date.today())
    return _records(
        returns_df[
            [
                "trade_date",
                "symbol",
                "usd_per_share",
                "current_price",
                "days_held",
                "total_return_pct",
                "annualized_return_pct",
                "hysa_period_return_pct",
                "alpha_period_pct",
                "usd_spent",
            ]
        ].rename(columns={"usd_per_share": "price_paid"})
    )


@app.get("/api/returns/curve")
def get_return_curve(as_of: date | None = None) -> dict:
    trades = _load_trades()
    returns_df = _build_returns(trades, as_of or date.today())
    trend_x, trend_y = returns.fit_trend(
        returns_df["days_held"].to_numpy(),
        returns_df["annualized_return_pct"].to_numpy(),
        returns_config,
    )
    return {
        "points": [
            {
                "symbol": row["symbol"],
                "days_held": row["days_held"],
                "annualized_return_pct": row["annualized_return_pct"],
            }
            for row in returns_df[["symbol", "days_held", "annualized_return_pct"]].to_dict(
                "records"
            )
        ],
        "trend": [
            {"days_held": float(x), "annualized_return_pct": float(y)}
            for x, y in zip(trend_x, trend_y, strict=True)
        ],
        "hysa_annual_rate_pct": returns_config.hysa_annual_rate * 100,
    }


@app.post("/api/sync")
def sync() -> dict:
    credentials = IbkrFlexCredentials()
    sync_result = api.sync_ibkr_account(credentials, ibkr_config)

    trades = _load_trades()
    symbols = sorted(trades["symbol"].unique())
    first_trade_date = trades["trade_date"].min().date()
    prices.update_price_caches(
        symbols, since=first_trade_date, as_of=date.today(), config=price_api_config
    )

    return {
        "synced_at": api.last_synced_at(ibkr_config).isoformat(),
        "new_event_count": sync_result.new_event_count,
        "total_event_count": sync_result.total_event_count,
        "symbols_refreshed": symbols,
    }
