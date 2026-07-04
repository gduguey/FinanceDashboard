"""JSON-over-HTTP view of the dashboard, computed entirely by `dashboard.py`/`ledger.*`.

Every endpoint below calls `dashboard.py` (which composes `ledger.*` and
`market_data.*`) and serializes the result — no aggregation happens in
this module itself, matching the split documented in
docs/architecture.md.

GET endpoints only ever read what's already cached on disk — they never
make a network call, with one exception: `GET /api/symbols/search` is a
live Yahoo Finance lookup for the benchmark picker's search box, which by
its nature needs a live answer rather than a cached one. `POST /sync` is
the endpoint that touches the network for the app's own data as a whole
(an IBKR pull plus a price/CPI/HYSA-rate cache refresh); that's what makes
the frontend's "Sync" button a real, explicit action instead of something
that silently happens on every page load. `POST
/api/symbols/{symbol}/ensure-priced` is the narrow exception to that: it
refreshes a single symbol's price cache on the spot, so picking a new
benchmark takes effect without waiting for a full sync.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from trades import dashboard
from trades.brokers.ibkr import api, main
from trades.config import AppConfig, IbkrFlexCredentials, TaxRegime
from trades.market_data import cpi as cpi_module
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.market_data import symbol_search as symbol_search_module

if TYPE_CHECKING:
    import polars as pl


@dataclass
class SyncProgress:
    """A snapshot of an in-flight (or just-finished) sync, for the frontend's progress bar."""

    step: str
    percent: float
    done: bool
    error: str | None = None


app = FastAPI(title="Investments API")
app.state.config = AppConfig()
app.state.sync_progress = SyncProgress(step="Idle", percent=0.0, done=True)


def _config() -> AppConfig:
    """Return the configuration attached to the running app.

    Held on `app.state` instead of a module-level variable so tests can
    swap it per-test through the app instance itself, rather than through
    mutable state shared across every import of this module.

    Returns
    -------
    AppConfig
        The application configuration currently attached to `app.state`.
    """
    return cast("AppConfig", app.state.config)


def _report_sync_progress(step: str, percent: float) -> None:
    app.state.sync_progress = SyncProgress(step=step, percent=percent, done=False)


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
    ledger = main.load_ledger(_config())
    if ledger.is_empty():
        message = "No ledger cached yet. Hit Sync to pull it from IBKR."
        raise HTTPException(status_code=404, detail=message)
    return ledger


def _first_event_date(ledger: pl.DataFrame) -> date:
    return cast("date", ledger["event_datetime"].dt.date().min())


def _chart_range(ledger: pl.DataFrame, start: date | None, end: date | None) -> tuple[date, date]:
    """Default a chart's range to the ledger's full history.

    Deliberately never defaults to "just today": a single-day snapshot
    invites watching daily noise, whereas since-inception or a long window
    shows the trend that actually matters.

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
    config = _config()
    last_synced = api.last_synced_at(config)
    return _to_display_zone(last_synced, config).isoformat() if last_synced else None


@app.get("/api/overview")
def get_overview(as_of: date | None = None) -> dict[str, Any]:
    """Return the overview card row: value, gain split, XIRR, dollar alpha, TWR.

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
        cards = dashboard.overview_cards(ledger, _config(), as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**asdict(cards), "last_synced_at": _last_synced_iso()}


@app.get("/api/chart/dollar")
def get_dollar_chart(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Return the three/four-line dollar chart plus reallocation markers.

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
        series = dashboard.dollar_chart_series(ledger, _config(), range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    markers = dashboard.reallocation_markers(ledger)
    return {"series": series.to_dicts(), "reallocation_markers": markers.to_dicts()}


@app.get("/api/chart/growth-of-100")
def get_growth_of_100_chart(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return the growth-of-$100 chart: NAV plus every benchmark, indexed to 100.

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
        return dashboard.growth_of_100_chart(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/monthly-pnl")
def get_monthly_pnl(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return each month's value change split into contributions and market gain.

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
        return dashboard.monthly_pnl(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/monthly-pnl/by-symbol")
def get_monthly_pnl_by_symbol(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Return each month's value change split into contributions and market gain, per symbol.

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
        return dashboard.monthly_pnl_by_symbol(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/allocation")
def get_allocation(as_of: date | None = None) -> list[dict[str, Any]]:
    """Return the current-value allocation by symbol (including cash), against the target.

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
        return dashboard.allocation_view(ledger, _config(), as_of or datetime.now(tz=UTC).date()).to_dicts()
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
    return dashboard.load_settings(_config()).target_allocation_pct


@app.put("/api/settings/target-allocation")
def put_target_allocation(target_allocation_pct: dict[str, float]) -> dict[str, float]:
    """Persist a new target allocation, set from the frontend.

    Merges into the existing settings — a settings file is one JSON blob,
    so writing this field naively from a fresh `DashboardSettings()` would
    silently wipe out the HYSA/benchmark settings saved separately.

    Returns
    -------
    dict[str, float]
        The persisted target allocation.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(update={"target_allocation_pct": target_allocation_pct})
    dashboard.save_settings(updated, config)
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
    settings = dashboard.load_settings(_config())
    return {"bank_id": settings.hysa_bank_id, "fixed_rate_pct": settings.hysa_fixed_rate_pct}


@app.put("/api/settings/hysa")
def put_hysa_settings(update: HysaSettingsUpdate) -> dict[str, Any]:
    """Persist a HYSA bank selection and/or fixed-rate override (merges into existing settings).

    Returns
    -------
    dict[str, Any]
        `bank_id`, `fixed_rate_pct` as persisted.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(
        update={"hysa_bank_id": update.bank_id, "hysa_fixed_rate_pct": update.fixed_rate_pct}
    )
    dashboard.save_settings(updated, config)
    return {"bank_id": updated.hysa_bank_id, "fixed_rate_pct": updated.hysa_fixed_rate_pct}


class BenchmarkSettingUpdate(BaseModel):
    """Request body for `PUT /api/settings/benchmark`."""

    symbol_override: str | None = None


@app.get("/api/settings/benchmark")
def get_benchmark_setting() -> dict[str, str | None]:
    """Return the persisted benchmark symbol override, plus the default it falls back to.

    Returns
    -------
    dict[str, str or None]
        `symbol_override` (None if never set) and `default_symbol` — the
        frontend needs the latter to display a concrete symbol even when
        no override is set.
    """
    config = _config()
    return {
        "symbol_override": dashboard.load_settings(config).benchmark_symbol_override,
        "default_symbol": config.returns.benchmark_symbol,
    }


@app.put("/api/settings/benchmark")
def put_benchmark_setting(update: BenchmarkSettingUpdate) -> dict[str, str | None]:
    """Persist a benchmark symbol override (merges into existing settings).

    Returns
    -------
    dict[str, str or None]
        `symbol_override` as persisted, plus `default_symbol`.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(update={"benchmark_symbol_override": update.symbol_override})
    dashboard.save_settings(updated, config)
    return {"symbol_override": updated.benchmark_symbol_override, "default_symbol": config.returns.benchmark_symbol}


class TaxSettingsUpdate(BaseModel):
    """Request body for `PUT /api/settings/tax`."""

    tax_enabled: bool
    tax_regime: TaxRegime | None
    residency_status_change_date: date | None
    w8ben_claimed: bool
    w8ben_treaty_rate_pct: float | None
    marginal_ordinary_rate_pct: float | None
    qualified_ltcg_rate_pct: float | None


def _tax_settings_response(config: AppConfig) -> dict[str, Any]:
    settings = dashboard.load_settings(config)
    return {
        "tax_enabled": settings.tax_enabled,
        "tax_regime": settings.tax_regime,
        "resolved_tax_regime": dashboard.resolved_tax_regime(config),
        "residency_status_change_date": settings.residency_status_change_date,
        "w8ben_claimed": settings.w8ben_claimed,
        "w8ben_treaty_rate_pct": settings.w8ben_treaty_rate_pct,
        "marginal_ordinary_rate_pct": settings.marginal_ordinary_rate_pct,
        "resolved_marginal_ordinary_rate_pct": dashboard.resolved_marginal_ordinary_rate(config) * 100,
        "qualified_ltcg_rate_pct": settings.qualified_ltcg_rate_pct,
        "resolved_qualified_ltcg_rate_pct": dashboard.resolved_qualified_ltcg_rate(config) * 100,
    }


@app.get("/api/settings/tax")
def get_tax_settings() -> dict[str, Any]:
    """Return the persisted tax-reporting settings.

    Returns
    -------
    dict[str, Any]
        `tax_enabled`, `tax_regime` (the raw selection, None if never
        set), `resolved_tax_regime` (what the tax report actually uses —
        `RESIDENT` when `tax_regime` is unset), `residency_status_change_date`,
        `w8ben_claimed`, `w8ben_treaty_rate_pct`, `marginal_ordinary_rate_pct`
        and `qualified_ltcg_rate_pct` (the raw overrides, None if never set)
        alongside their `resolved_*_pct` counterparts (what the tax report
        actually uses — the code default when no override was made).
    """
    return _tax_settings_response(_config())


@app.put("/api/settings/tax")
def put_tax_settings(update: TaxSettingsUpdate) -> dict[str, Any]:
    """Persist tax-reporting settings (merges into existing settings).

    Returns
    -------
    dict[str, Any]
        Same shape as `GET /api/settings/tax`, reflecting what was just persisted.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(
        update={
            "tax_enabled": update.tax_enabled,
            "tax_regime": update.tax_regime,
            "residency_status_change_date": update.residency_status_change_date,
            "w8ben_claimed": update.w8ben_claimed,
            "w8ben_treaty_rate_pct": update.w8ben_treaty_rate_pct,
            "marginal_ordinary_rate_pct": update.marginal_ordinary_rate_pct,
            "qualified_ltcg_rate_pct": update.qualified_ltcg_rate_pct,
        }
    )
    dashboard.save_settings(updated, config)
    return _tax_settings_response(config)


@app.get("/api/tax/report")
def get_tax_report(as_of: date | None = None) -> dict[str, Any]:
    """Return the full tax view: the annual report, estimated tax owed, flagged wash sales, and sale previews.

    Returns
    -------
    dict[str, Any]
        `annual`, `tax_owed` (the annual report plus estimated
        `capital_gains_tax_usd`, `dividend_tax_usd`, `total_tax_usd`,
        `balance_due_usd` per year), `wash_sales`, `sale_previews`,
        `after_tax_dollar_alpha_vs_hysa_usd`, and the liquidation estimate —
        `liquidation_pretax_value_usd`, `liquidation_long_term_gain_usd`,
        `liquidation_short_term_gain_usd`, `liquidation_capital_gains_tax_usd`,
        `liquidation_value_usd` — what a full sale of every open lot right
        now would leave you with, and the arithmetic behind that number.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger()
    try:
        summary = dashboard.tax_summary(ledger, _config(), as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "annual": summary.annual.to_dicts(),
        "tax_owed": summary.tax_owed.to_dicts(),
        "wash_sales": summary.wash_sales.to_dicts(),
        "sale_previews": summary.sale_previews.to_dicts(),
        "after_tax_dollar_alpha_vs_hysa_usd": summary.after_tax_dollar_alpha_vs_hysa_usd,
        "liquidation_pretax_value_usd": summary.liquidation_pretax_value_usd,
        "liquidation_long_term_gain_usd": summary.liquidation_long_term_gain_usd,
        "liquidation_short_term_gain_usd": summary.liquidation_short_term_gain_usd,
        "liquidation_capital_gains_tax_usd": summary.liquidation_capital_gains_tax_usd,
        "liquidation_value_usd": summary.liquidation_value_usd,
    }


@app.get("/api/hysa-rates")
def get_hysa_rates() -> dict[str, Any]:
    """Return every bank's known rate history, for the bank picker and APY comparison chart.

    Returns
    -------
    dict[str, Any]
        `banks` (id/name pairs), `history` (every rate-change row), `default_bank_id`.
    """
    config = _config()
    history = hysa_rates_module.load_hysa_rates_cache(config)
    banks = hysa_rates_module.list_banks(history)
    return {
        "banks": banks.to_dicts(),
        "history": history.to_dicts(),
        "default_bank_id": config.hysa_rates.default_bank_id,
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
    return symbol_search_module.search_symbols(q, _config())


@app.post("/api/symbols/{symbol}/ensure-priced")
def ensure_symbol_priced(symbol: str) -> dict[str, Any]:
    """Refresh one symbol's price cache if it isn't already current, without a full sync.

    Lets picking a new benchmark symbol take effect immediately —
    `prices.update_price_cache` only fetches whatever date range is
    actually missing, so re-running this on an already-current symbol is
    cheap and safe to call on every selection.

    Returns
    -------
    dict[str, Any]
        `symbol`, `was_stale` (whether a fetch was actually needed), `last_price_date`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if Yahoo Finance has no data for `symbol`.
    """
    config = _config()
    ledger = _load_ledger()
    first_event = _first_event_date(ledger)
    today = datetime.now(tz=UTC).date()

    existing = prices.load_price_cache(symbol, config)
    was_stale = existing.is_empty() or cast("date", existing["price_date"].max()) < today
    try:
        prices.update_price_cache(symbol, since=first_event, as_of=today, config=config)
        updated = prices.update_price_cache(symbol, since=first_event, as_of=today, config=config, adjusted=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    last_price_date = updated["price_date"].max() if not updated.is_empty() else None
    return {
        "symbol": symbol,
        "was_stale": was_stale,
        "last_price_date": str(last_price_date) if last_price_date else None,
    }


@app.get("/api/lots")
def get_lots(as_of: date | None = None) -> dict[str, Any]:
    """Return the trade-level table: open lots, closed lots, per-symbol rollup.

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
        table = dashboard.lots_table(ledger, _config(), as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "open_lots": table.open_lots.to_dicts(),
        "closed_lots": table.closed_lots.to_dicts(),
        "symbol_rollup": table.symbol_rollup.to_dicts(),
    }


@app.get("/api/risk")
def get_risk(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Return the largest peak-to-trough NAV decline over a window.

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
        return {"max_drawdown_pct": dashboard.risk_stat(ledger, _config(), range_start, range_end)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/data-quality")
def get_data_quality() -> list[dict[str, Any]]:
    """Return the last cached price date per symbol ever held or benchmarked against.

    Returns
    -------
    list[dict[str, Any]]
        One entry per symbol.
    """
    config = _config()
    ledger = _load_ledger()
    held_and_benchmark = {*ledger["symbol"].unique().to_list(), dashboard.resolved_benchmark_symbol(config)}
    symbols = sorted(held_and_benchmark - {config.ledger.cash_symbol})
    return dashboard.data_quality(symbols, config).to_dicts()


@app.get("/api/ledger/export")
def get_ledger_export() -> list[dict[str, Any]]:
    """Export the full ledger, for the user's own backup.

    Returns
    -------
    list[dict[str, Any]]
        Every ledger row.
    """
    return _load_ledger().to_dicts()


@app.get("/api/sync/progress")
def get_sync_progress() -> dict[str, Any]:
    """Return the current (or most recently finished) sync's progress.

    Polled by the frontend's progress bar while a sync is running.
    `POST /api/sync` runs in FastAPI's thread pool (it's a plain `def`,
    not `async def`), so this GET is served concurrently on its own
    thread rather than queued behind the sync request.

    Returns
    -------
    dict[str, Any]
        `step`, `percent`, `done`, `error`.
    """
    return asdict(cast("SyncProgress", app.state.sync_progress))


def _run_sync(config: AppConfig) -> dict[str, Any]:
    _report_sync_progress("Connecting to IBKR", 0.0)
    credentials = IbkrFlexCredentials()  # type: ignore[call-arg]  # token/query_id come from the environment
    sync_result = main.sync_ibkr_account(credentials, config, on_progress=_report_sync_progress)

    ledger = _load_ledger()
    benchmark_symbol = dashboard.resolved_benchmark_symbol(config)
    held_symbols = sorted(set(ledger["symbol"].unique().to_list()) - {config.ledger.cash_symbol})
    raw_symbols = sorted({*held_symbols, benchmark_symbol})
    first_event = _first_event_date(ledger)
    today = datetime.now(tz=UTC).date()

    _report_sync_progress("Updating price history", 65.0)
    prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=config)
    _report_sync_progress("Updating benchmark prices", 80.0)
    prices.update_price_cache(benchmark_symbol, since=first_event, as_of=today, config=config, adjusted=True)
    _report_sync_progress("Updating CPI index", 90.0)
    cpi_module.update_cpi_cache(config)
    _report_sync_progress("Updating savings rates", 95.0)
    hysa_rates_module.update_hysa_rates_cache(config)

    return {
        "synced_at": _last_synced_iso(),
        "new_event_count": sync_result.new_event_count,
        "total_event_count": sync_result.total_event_count,
        "symbols_refreshed": raw_symbols,
    }


@app.post("/api/sync")
def sync() -> dict[str, Any]:
    """Pull the latest IBKR statement and refresh the price/CPI/HYSA-rate caches.

    Refreshes the raw price cache for every symbol ever held plus the
    benchmark symbol, the adjusted (dividend-reinvested) cache for the
    benchmark symbol only (adjusted prices are for benchmark
    counterfactuals, never for pricing your own positions), the CPI
    cache, and every bank's HYSA rate history. Reports progress to
    `app.state.sync_progress` throughout, readable via `GET
    /api/sync/progress` — the IBKR pull is the one step slow enough that a
    bare spinner isn't good enough feedback.

    Returns
    -------
    dict[str, Any]
        `synced_at`, `new_event_count`, `total_event_count`, `symbols_refreshed`.
    """
    try:
        result = _run_sync(_config())
    except Exception as error:
        app.state.sync_progress = SyncProgress(step="Sync failed", percent=100.0, done=True, error=str(error))
        raise

    app.state.sync_progress = SyncProgress(step="Done", percent=100.0, done=True)
    return result
