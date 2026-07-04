"""JSON-over-HTTP view of the dashboard, computed entirely by `dashboard.py`/`ledger.*`.

Every endpoint below calls `dashboard.py` (which composes `ledger.*` and
`market_data.*`) and serializes the result — no aggregation happens in
this module itself, matching the split documented in
docs/architecture.md.

GET endpoints only ever read what's already cached on disk — they never
make a network call, with one exception: `GET /api/symbols/search` is a
live Yahoo Finance lookup for the benchmark picker's search box, which by
its nature needs a live answer rather than a cached one. `POST /sync` is
the one endpoint allowed to touch the network for the app's own data (an
IBKR pull plus a price/CPI/HYSA-rate cache refresh); that's what makes the
frontend's "Sync" button a real, explicit action instead of something
that silently happens on every page load.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from trades import dashboard
from trades.brokers.ibkr import api, main
from trades.config import AppConfig, IbkrFlexCredentials
from trades.market_data import cpi as cpi_module
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.market_data import symbol_search as symbol_search_module

if TYPE_CHECKING:
    import polars as pl

app_config = AppConfig()

app = FastAPI(title="Investments API")


def _load_ledger() -> pl.DataFrame:
    """Load the cached ledger. Read-only — never touches the network.

    Returns
    -------
    polars.DataFrame
        The full ledger, in chronological order.

    Raises
    ------
    HTTPException
        If no ledger has been cached yet (404).
    """
    ledger = main.load_ledger(app_config)
    if ledger.is_empty():
        message = "No ledger cached yet. Hit Sync to pull it from IBKR."
        raise HTTPException(status_code=404, detail=message)
    return ledger


def _first_event_date(ledger: pl.DataFrame) -> date:
    return cast("date", ledger["event_datetime"].dt.date().min())


def _chart_range(ledger: pl.DataFrame, start: date | None, end: date | None) -> tuple[date, date]:
    """Default a chart's range to the ledger's full history.

    Never defaults to "just today" (NEW_TASKS.md 6.10): the dashboard's
    views should default to since-inception or a long window, not a
    single-day snapshot that invites daily-change watching.

    Returns
    -------
    tuple[datetime.date, datetime.date]
        `(start, end)`, each defaulted if not given.
    """
    return start or _first_event_date(ledger), end or datetime.now(tz=UTC).date()


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


def _last_synced_iso() -> str | None:
    last_synced = api.last_synced_at(app_config)
    return _to_display_zone(last_synced, app_config).isoformat() if last_synced else None


@app.get("/api/overview")
def get_overview(as_of: date | None = None) -> dict[str, Any]:
    """Return the overview card row: value, gain split, XIRR, dollar alpha, TWR (NEW_TASKS.md 6.1).

    Returns
    -------
    dict[str, Any]
        `OverviewCards` fields, plus `last_synced_at`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    try:
        cards = dashboard.overview_cards(ledger, app_config, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**asdict(cards), "last_synced_at": _last_synced_iso()}


@app.get("/api/chart/dollar")
def get_dollar_chart(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Return the three/four-line dollar chart plus reallocation markers (NEW_TASKS.md 6.2).

    Returns
    -------
    dict[str, Any]
        `series` (one entry per day) and `reallocation_markers`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        series = dashboard.dollar_chart_series(ledger, app_config, range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    markers = dashboard.reallocation_markers(ledger)
    return {"series": series.to_dicts(), "reallocation_markers": markers.to_dicts()}


@app.get("/api/chart/growth-of-100")
def get_growth_of_100_chart(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return the growth-of-$100 chart: NAV plus every benchmark, indexed to 100 (NEW_TASKS.md 3.3, 6.3).

    Returns
    -------
    list[dict[str, Any]]
        One entry per day.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.growth_of_100_chart(ledger, app_config, range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/monthly-pnl")
def get_monthly_pnl(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return each month's value change split into contributions and market gain (NEW_TASKS.md 3.5, 6.4).

    Returns
    -------
    list[dict[str, Any]]
        One entry per calendar month.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.monthly_pnl(ledger, app_config, range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/monthly-pnl/by-symbol")
def get_monthly_pnl_by_symbol(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return each month's value change split into contributions and market gain, per symbol (NEW_TASKS.md 3.5, 6.4).

    Returns
    -------
    list[dict[str, Any]]
        One entry per (month, symbol) pair.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.monthly_pnl_by_symbol(ledger, app_config, range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/allocation")
def get_allocation(as_of: date | None = None) -> list[dict[str, Any]]:
    """Return the current-value allocation by symbol (including cash), against the target (NEW_TASKS.md 6.5).

    Returns
    -------
    list[dict[str, Any]]
        One entry per symbol (plus cash).

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    try:
        return dashboard.allocation_view(ledger, app_config, as_of or datetime.now(tz=UTC).date()).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/settings/target-allocation")
def get_target_allocation() -> dict[str, float]:
    """Return the persisted target allocation.

    Returns
    -------
    dict[str, float]
        Symbol -> target percentage.
    """
    return dashboard.load_settings(app_config).target_allocation_pct


@app.put("/api/settings/target-allocation")
def put_target_allocation(target_allocation_pct: dict[str, float]) -> dict[str, float]:
    """Persist a new target allocation, set from the frontend (NEW_TASKS.md 6.5).

    Merges into the existing settings — a settings file is one JSON blob,
    so writing this field naively from a fresh `DashboardSettings()` would
    silently wipe out the HYSA/benchmark settings saved separately.

    Returns
    -------
    dict[str, float]
        The persisted target allocation.
    """
    updated = dashboard.load_settings(app_config).model_copy(update={"target_allocation_pct": target_allocation_pct})
    dashboard.save_settings(updated, app_config)
    return updated.target_allocation_pct


class HysaSettingsUpdate(BaseModel):
    """Request body for `PUT /api/settings/hysa`."""

    bank_id: str | None = None
    fixed_rate_pct: float | None = None


@app.get("/api/settings/hysa")
def get_hysa_settings() -> dict[str, Any]:
    """Return the persisted HYSA bank selection / fixed-rate override.

    Returns
    -------
    dict[str, Any]
        `bank_id`, `fixed_rate_pct` — both None if never set.
    """
    settings = dashboard.load_settings(app_config)
    return {"bank_id": settings.hysa_bank_id, "fixed_rate_pct": settings.hysa_fixed_rate_pct}


@app.put("/api/settings/hysa")
def put_hysa_settings(update: HysaSettingsUpdate) -> dict[str, Any]:
    """Persist a HYSA bank selection and/or fixed-rate override (merges into existing settings).

    Returns
    -------
    dict[str, Any]
        `bank_id`, `fixed_rate_pct` as persisted.
    """
    updated = dashboard.load_settings(app_config).model_copy(
        update={"hysa_bank_id": update.bank_id, "hysa_fixed_rate_pct": update.fixed_rate_pct}
    )
    dashboard.save_settings(updated, app_config)
    return {"bank_id": updated.hysa_bank_id, "fixed_rate_pct": updated.hysa_fixed_rate_pct}


class BenchmarkSettingUpdate(BaseModel):
    """Request body for `PUT /api/settings/benchmark`."""

    symbol_override: str | None = None


@app.get("/api/settings/benchmark")
def get_benchmark_setting() -> dict[str, str | None]:
    """Return the persisted benchmark symbol override.

    Returns
    -------
    dict[str, str or None]
        `symbol_override` — None if never set (falls back to `config.returns.benchmark_symbol`).
    """
    return {"symbol_override": dashboard.load_settings(app_config).benchmark_symbol_override}


@app.put("/api/settings/benchmark")
def put_benchmark_setting(update: BenchmarkSettingUpdate) -> dict[str, str | None]:
    """Persist a benchmark symbol override (merges into existing settings).

    Returns
    -------
    dict[str, str or None]
        `symbol_override` as persisted.
    """
    updated = dashboard.load_settings(app_config).model_copy(
        update={"benchmark_symbol_override": update.symbol_override}
    )
    dashboard.save_settings(updated, app_config)
    return {"symbol_override": updated.benchmark_symbol_override}


@app.get("/api/hysa-rates")
def get_hysa_rates() -> dict[str, Any]:
    """Return every bank's known rate history, for the bank picker and APY comparison chart.

    Returns
    -------
    dict[str, Any]
        `banks` (id/name pairs), `history` (every rate-change row), `default_bank_id`.
    """
    history = hysa_rates_module.load_hysa_rates_cache(app_config)
    banks = hysa_rates_module.list_banks(history)
    return {
        "banks": banks.to_dicts(),
        "history": history.to_dicts(),
        "default_bank_id": app_config.hysa_rates.default_bank_id,
    }


@app.get("/api/symbols/search")
def get_symbol_search(q: str) -> list[dict[str, str]]:
    """Search Yahoo Finance for a ticker symbol, for the benchmark picker.

    Unlike every other GET endpoint, this touches the network — a live
    search box needs a live answer, and there's nothing here to cache.

    Returns
    -------
    list[dict[str, str]]
        One entry per match: `symbol`, `name`, `exchange`.
    """
    return symbol_search_module.search_symbols(q, app_config)


@app.get("/api/lots")
def get_lots(as_of: date | None = None) -> dict[str, Any]:
    """Return the trade-level table: open lots, closed lots, per-symbol rollup (NEW_TASKS.md 6.6).

    Returns
    -------
    dict[str, Any]
        `open_lots`, `closed_lots`, `symbol_rollup`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    try:
        table = dashboard.lots_table(ledger, app_config, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "open_lots": table.open_lots.to_dicts(),
        "closed_lots": table.closed_lots.to_dicts(),
        "symbol_rollup": table.symbol_rollup.to_dicts(),
    }


@app.get("/api/risk")
def get_risk(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Return the largest peak-to-trough NAV decline over a window (NEW_TASKS.md 6.7).

    Returns
    -------
    dict[str, Any]
        `max_drawdown_pct`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return {"max_drawdown_pct": dashboard.risk_stat(ledger, app_config, range_start, range_end)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/data-quality")
def get_data_quality() -> list[dict[str, Any]]:
    """Return the last cached price date per symbol ever held or benchmarked against (NEW_TASKS.md 6.9).

    Returns
    -------
    list[dict[str, Any]]
        One entry per symbol.
    """
    ledger = _load_ledger()
    held_and_benchmark = {*ledger["symbol"].unique().to_list(), dashboard.resolved_benchmark_symbol(app_config)}
    symbols = sorted(held_and_benchmark - {app_config.ledger.cash_symbol})
    return dashboard.data_quality(symbols, app_config).to_dicts()


@app.get("/api/ledger/export")
def get_ledger_export() -> list[dict[str, Any]]:
    """Export the full ledger, for the user's own backup (NEW_TASKS.md 6.9).

    Returns
    -------
    list[dict[str, Any]]
        Every ledger row.
    """
    return _load_ledger().to_dicts()


@app.post("/api/sync")
def sync() -> dict[str, Any]:
    """Pull the latest IBKR statement and refresh the price/CPI/HYSA-rate caches.

    Refreshes the raw price cache for every symbol ever held plus the
    benchmark symbol, the adjusted (dividend-reinvested) cache for the
    benchmark symbol only (0.5: adjusted prices are for benchmark
    counterfactuals, never for pricing your own positions), the CPI
    cache, and every bank's HYSA rate history.

    Returns
    -------
    dict[str, Any]
        `synced_at`, `new_event_count`, `total_event_count`, `symbols_refreshed`.
    """
    credentials = IbkrFlexCredentials()  # type: ignore[call-arg]  # token/query_id come from the environment
    sync_result = main.sync_ibkr_account(credentials, app_config)

    ledger = _load_ledger()
    benchmark_symbol = dashboard.resolved_benchmark_symbol(app_config)
    held_symbols = sorted(set(ledger["symbol"].unique().to_list()) - {app_config.ledger.cash_symbol})
    raw_symbols = sorted({*held_symbols, benchmark_symbol})
    first_event = _first_event_date(ledger)
    today = datetime.now(tz=UTC).date()

    prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=app_config)
    prices.update_price_cache(benchmark_symbol, since=first_event, as_of=today, config=app_config, adjusted=True)
    cpi_module.update_cpi_cache(app_config)
    hysa_rates_module.update_hysa_rates_cache(app_config)

    return {
        "synced_at": _last_synced_iso(),
        "new_event_count": sync_result.new_event_count,
        "total_event_count": sync_result.total_event_count,
        "symbols_refreshed": raw_symbols,
    }
