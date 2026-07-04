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

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException

from trades import prices, returns, transactions
from trades.brokers.ibkr import api, main
from trades.config import AppConfig, IbkrFlexCredentials

if TYPE_CHECKING:
    import polars as pl

app_config = AppConfig()

app = FastAPI(title="Investments API")


def _load_trades() -> pl.DataFrame:
    """Load the ledger, standardize it to the trade schema, enrich, and aggregate.

    Read-only — never touches the network.

    Returns
    -------
    polars.DataFrame
        One row per same-day, same-symbol-price cluster of `BUY` events.

    Raises
    ------
    HTTPException
        If no ledger has been cached yet (404).
    """
    ledger = main.load_ledger(app_config)
    if ledger.is_empty():
        message = "No ledger cached yet. Hit Sync to pull it from IBKR."
        raise HTTPException(status_code=404, detail=message)
    standardized = transactions.standardize_ibkr_trades(ledger.lazy(), app_config)
    enriched = transactions.enrich_trades(standardized)
    aggregated = transactions.aggregate_same_day_trades(enriched, app_config)
    return cast("pl.LazyFrame", aggregated).collect()


def _price_lookup(symbol: str, as_of: date) -> float | None:
    history = prices.load_price_cache(symbol, app_config)
    return prices.price_as_of(history, as_of)


def _build_returns(trades: pl.DataFrame, as_of: date) -> pl.DataFrame:
    try:
        return cast("pl.DataFrame", returns.build_returns_table(trades, _price_lookup, as_of=as_of, config=app_config))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _to_display_zone(value: datetime, config: AppConfig) -> datetime:
    """Attach the display timezone to a naive-UTC datetime, for human-facing output.

    Parameters
    ----------
    value
        A naive UTC datetime (the storage format everywhere in this app).
    config
        Application configuration; `config.timezone.local_zone` is read.

    Returns
    -------
    datetime.datetime
        `value` converted to `config.timezone.local_zone` and made
        tz-aware, so its `isoformat()` carries a real UTC offset.
    """
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(config.timezone.local_zone))


@app.get("/api/summary")
def get_summary() -> dict[str, Any]:
    """Return portfolio-level totals: invested, current value, gain, and alpha.

    Returns
    -------
    dict[str, Any]
        `as_of_date`, `total_invested_usd`, `current_value_usd`,
        `total_gain_usd`, `total_gain_pct`, `portfolio_alpha_pct`,
        `hysa_annual_rate`, `symbol_count`, `last_synced_at`.
    """
    today = datetime.now(tz=UTC).date()
    trades = _load_trades()
    returns_df = _build_returns(trades, today)

    total_invested = float(trades["usd_spent"].sum())
    current_value = float((returns_df["shares"] * returns_df["current_price"]).sum())
    total_gain_usd = current_value - total_invested

    last_synced = api.last_synced_at(app_config)

    return {
        "as_of_date": today.isoformat(),
        "total_invested_usd": total_invested,
        "current_value_usd": current_value,
        "total_gain_usd": total_gain_usd,
        "total_gain_pct": (total_gain_usd / total_invested * 100) if total_invested else None,
        "portfolio_alpha_pct": returns.portfolio_alpha_pct(returns_df),
        "hysa_annual_rate": app_config.returns.hysa_annual_rate,
        "symbol_count": trades["symbol"].n_unique(),
        "last_synced_at": _to_display_zone(last_synced, app_config).isoformat() if last_synced else None,
    }


@app.get("/api/trades")
def get_trades() -> list[dict[str, Any]]:
    """Return every aggregated trade row.

    Returns
    -------
    list[dict[str, Any]]
        One entry per same-day, same-symbol-price cluster of `BUY` events.
    """
    return _load_trades().to_dicts()


@app.get("/api/schedule/monthly")
def get_monthly_invested() -> list[dict[str, Any]]:
    """Return USD invested per month, broken down by symbol.

    Returns
    -------
    list[dict[str, Any]]
        One entry per calendar month.
    """
    return cast("pl.DataFrame", transactions.monthly_invested(_load_trades())).to_dicts()


@app.get("/api/schedule/daily")
def get_daily_timeline() -> list[dict[str, Any]]:
    """Return USD invested per investment day, with a running total.

    Returns
    -------
    list[dict[str, Any]]
        One entry per day a trade happened.
    """
    return cast("pl.DataFrame", transactions.daily_investment_timeline(_load_trades())).to_dicts()


@app.get("/api/schedule/pie")
def get_pie_breakdown() -> dict[str, list[dict[str, Any]]]:
    """Return every pie-chart breakdown: whole-portfolio-by-symbol plus one by-date breakdown per symbol.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Chart label -> its breakdown rows.
    """
    options = transactions.pie_chart_options(_load_trades())
    return {label: breakdown.to_dicts() for label, breakdown in options.items()}


@app.get("/api/returns")
def get_returns(as_of: date | None = None) -> list[dict[str, Any]]:
    """Return the per-trade returns table as of a given date (today, by default).

    Returns
    -------
    list[dict[str, Any]]
        One entry per trade.
    """
    trades = _load_trades()
    returns_df = _build_returns(trades, as_of or datetime.now(tz=UTC).date())
    selected = returns_df.select(
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
    ).rename({"usd_per_share": "price_paid"})
    return selected.to_dicts()


@app.get("/api/returns/curve")
def get_return_curve(as_of: date | None = None) -> dict[str, Any]:
    """Return per-trade annualized return vs. days held, plus a fitted trend line.

    Returns
    -------
    dict[str, Any]
        `points` (one per trade), `trend` (the fitted curve), `hysa_annual_rate_pct`.
    """
    trades = _load_trades()
    returns_df = _build_returns(trades, as_of or datetime.now(tz=UTC).date())
    trend_x, trend_y = returns.fit_trend(
        returns_df["days_held"].to_numpy(),
        returns_df["annualized_return_pct"].to_numpy(),
        app_config,
    )
    return {
        "points": returns_df.select("symbol", "days_held", "annualized_return_pct").to_dicts(),
        "trend": [
            {"days_held": float(x), "annualized_return_pct": float(y)} for x, y in zip(trend_x, trend_y, strict=True)
        ],
        "hysa_annual_rate_pct": app_config.returns.hysa_annual_rate * 100,
    }


@app.post("/api/sync")
def sync() -> dict[str, Any]:
    """Pull the latest IBKR statement and refresh the price cache for every known symbol.

    Returns
    -------
    dict[str, Any]
        `synced_at`, `new_event_count`, `total_event_count`, `symbols_refreshed`.
    """
    credentials = IbkrFlexCredentials()  # type: ignore[call-arg]  # token/query_id come from the environment
    sync_result = main.sync_ibkr_account(credentials, app_config)

    trades = _load_trades()
    symbols = sorted(trades["symbol"].unique())
    first_trade_date = cast("date", trades["trade_date"].min())
    prices.update_price_caches(symbols, since=first_trade_date, as_of=datetime.now(tz=UTC).date(), config=app_config)

    last_synced = api.last_synced_at(app_config)
    return {
        "synced_at": _to_display_zone(last_synced, app_config).isoformat() if last_synced else None,
        "new_event_count": sync_result.new_event_count,
        "total_event_count": sync_result.total_event_count,
        "symbols_refreshed": symbols,
    }
