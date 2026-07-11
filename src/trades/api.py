"""JSON-over-HTTP view of the dashboard, computed entirely by `dashboard.py`/`ledger.*`.

Every endpoint below calls `dashboard.py` (which composes `ledger.*` and
`market_data.*`) and serializes the result — no aggregation happens in
this module itself, matching the split documented in
docs/trades/architecture.md.

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

import io
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Any, cast
from zoneinfo import ZoneInfo

import requests
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from accounting.api import router as accounting_router
from db.session import get_db
from trades import dashboard
from trades.brokers.ibkr import api, main
from trades.config import AppConfig, TaxRegime
from trades.credentials import (
    IbkrCredentialOverride,
    ibkr_is_configured,
    load_ibkr_credential_override,
    resolve_ibkr_credentials,
    save_ibkr_credential_override,
)
from trades.market_data import cpi as cpi_module
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.market_data import symbol_search as symbol_search_module
from trades.utils.frames import collect_if_lazy
from trades.utils.statement_archive import DEFAULT_USER_ID, StatementArchive

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
app.include_router(accounting_router)

# Same layout in the Docker image (built by the frontend-builder stage into
# web/dist/) and in a local dev checkout (built by hand via `npm run build`)
# — both put this file at src/trades/api.py, two levels under the repo root.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

# Lock to prevent concurrent syncs: /api/sync writes to caches and the ledger,
# so concurrent requests would step on each other's writes.
_sync_lock = Lock()


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


def _load_ledger(session: Session) -> pl.DataFrame:
    """Load the cached ledger. Read-only — never touches the network.

    Parameters
    ----------
    session
        An open database session.

    Returns
    -------
    polars.DataFrame
        The full ledger, in chronological order.

    Raises
    ------
    HTTPException
        If no ledger has been cached yet (404).
    """
    ledger = main.load_ledger(session)
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
def get_overview(session: Annotated[Session, Depends(get_db)], as_of: date | None = None) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
    try:
        cards = dashboard.overview_cards(ledger, _config(), as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**asdict(cards), "last_synced_at": _last_synced_iso()}


@app.get("/api/chart/dollar")
def get_dollar_chart(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        series = dashboard.dollar_chart_series(ledger, _config(), range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    markers = dashboard.reallocation_markers(ledger)
    return {"series": series.to_dicts(), "reallocation_markers": markers.to_dicts()}


@app.get("/api/chart/growth-of-100")
def get_growth_of_100_chart(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
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
    ledger = _load_ledger(session)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.growth_of_100_chart(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/cash-history")
def get_cash_history(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
    """Return the uninvested cash balance for every day in range, plus what it would be worth invested immediately.

    Returns
    -------
    list[dict[str, Any]]
        One `{"date", "cash", "benchmark_live_usd", "benchmark_realized_usd",
        "hysa_live_usd", "hysa_realized_usd"}` entry per day.
        `*_live_usd` is what currently-sitting cash would be worth had it
        been invested in the benchmark/HYSA since it arrived — bounded,
        tracks `cash`'s own shape. `*_realized_usd` is a running total,
        banked once per past sitting episode at the moment it ended, of
        the gain that episode's cash missed out on — frozen from then on
        (see `dashboard.cash_received_counterfactual`).

    Raises
    ------
    HTTPException
        Via `_load_ledger`, if no ledger is cached yet (404).
    """
    ledger = _load_ledger(session)
    config = _config()
    range_start, range_end = _chart_range(ledger, start, end)
    daily_cash = cast("pl.DataFrame", dashboard.daily_cash_balances(ledger, config, range_start, range_end))
    adjusted_lookup = dashboard.make_price_lookup(config, adjusted=True)
    benchmark_symbol = dashboard.resolved_benchmark_symbol(config)
    try:
        counterfactual = dashboard.cash_received_counterfactual(
            daily_cash,
            benchmark_price_lookup=lambda day: adjusted_lookup(benchmark_symbol, day),
            hysa_rate_lookup=dashboard.hysa_rate_lookup(config),
            days_per_year=config.returns.days_per_year,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return daily_cash.join(counterfactual, on="date", how="left").to_dicts()


@app.get("/api/cash-sitting")
def get_cash_sitting(session: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """Report how long the current uninvested cash balance has been sitting idle, and what it's missed out on.

    Returns
    -------
    dict[str, Any]
        See `dashboard.cash_sitting.CashSittingSummary` — `sitting_since` as an ISO date string.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session)
    config = _config()
    today = datetime.now(tz=UTC).date()
    range_start = _first_event_date(ledger)
    try:
        daily_cash = cast("pl.DataFrame", dashboard.daily_cash_balances(ledger, config, range_start, today))
        growth_index = dashboard.growth_of_100_chart(ledger, config, range_start, today)
        summary = dashboard.cash_sitting_summary(daily_cash, growth_index, today, config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**asdict(summary), "sitting_since": summary.sitting_since.isoformat()}


@app.get("/api/chart/monthly-pnl")
def get_monthly_pnl(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
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
    ledger = _load_ledger(session)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.monthly_pnl(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/chart/monthly-pnl/by-symbol")
def get_monthly_pnl_by_symbol(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> list[dict[str, Any]]:
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
    ledger = _load_ledger(session)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return dashboard.monthly_pnl_by_symbol(ledger, _config(), range_start, range_end).to_dicts()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/allocation")
def get_allocation(session: Annotated[Session, Depends(get_db)], as_of: date | None = None) -> list[dict[str, Any]]:
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
    ledger = _load_ledger(session)
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


class IbkrCredentialsUpdate(BaseModel):
    """Request body for `PUT /api/settings/ibkr`.

    Either field left `None` leaves that one exactly as it was — a query
    id entered with no token doesn't clear an existing token, the same
    partial-merge convention `PUT /api/settings/benchmark`/`/tax` use.
    """

    token: str | None = None
    query_id: str | None = None


@app.get("/api/settings/ibkr")
def get_ibkr_settings() -> dict[str, Any]:
    """Report whether IBKR credentials are available, without ever exposing their value.

    Returns
    -------
    dict[str, Any]
        `configured` (true if a token and query id are available from
        either the Settings-page override or `.env`), `token_set` and
        `query_id_set` (whether the Settings-page override itself has
        each field, regardless of `.env`).
    """
    config = _config()
    override = load_ibkr_credential_override(config)
    return {
        "configured": ibkr_is_configured(config),
        "token_set": bool(override.token),
        "query_id_set": bool(override.query_id),
    }


@app.put("/api/settings/ibkr")
def put_ibkr_settings(update: IbkrCredentialsUpdate) -> dict[str, Any]:
    """Persist an IBKR credential override (merges into the existing one).

    Returns
    -------
    dict[str, Any]
        Same shape as `GET /api/settings/ibkr`, reflecting what was just persisted.
    """
    config = _config()
    existing = load_ibkr_credential_override(config)
    updated = existing.model_copy(
        update={
            "token": update.token if update.token is not None else existing.token,
            "query_id": update.query_id if update.query_id is not None else existing.query_id,
        }
    )
    save_ibkr_credential_override(updated, config)
    return {
        "configured": ibkr_is_configured(config),
        "token_set": bool(updated.token),
        "query_id_set": bool(updated.query_id),
    }


@app.delete("/api/settings/ibkr")
def delete_ibkr_settings() -> dict[str, Any]:
    """Clear the Settings-page IBKR credential override, falling back to `.env` (if any) again.

    Returns
    -------
    dict[str, Any]
        Same shape as `GET /api/settings/ibkr`.
    """
    config = _config()
    save_ibkr_credential_override(IbkrCredentialOverride(), config)
    return {"configured": ibkr_is_configured(config), "token_set": False, "query_id_set": False}


@app.post("/api/settings/ibkr/verify")
def verify_ibkr_settings() -> dict[str, Any]:
    """Actually attempt to authenticate with IBKR, not just check that something's typed in.

    A single fast HTTP call (see `verify_flex_credentials`) — not a full
    sync — so this is cheap enough for the Settings page to call whenever
    it wants a real "does this work" answer instead of "is this set".

    Returns
    -------
    dict[str, Any]
        `ok` (whether IBKR accepted the token/query id) and `error`
        (IBKR's own message, or a generic one, only when `ok` is false).
    """
    config = _config()
    try:
        credentials = resolve_ibkr_credentials(config)
    except ValidationError:
        return {"ok": False, "error": "No credentials configured"}
    try:
        api.verify_flex_credentials(credentials, config)
    except api.FlexApiError as error:
        return {"ok": False, "error": error.message}
    except Exception:  # noqa: BLE001 — surfacing any failure to the caller is the entire point here
        # Not str(error): a connection/HTTP error's own message includes the
        # full request URL, which embeds the token as a query param (see
        # `_send_flex_request`) — that must never round-trip back to the client.
        return {"ok": False, "error": "Could not reach IBKR to verify credentials"}
    return {"ok": True, "error": None}


@app.get("/api/tax/report")
def get_tax_report(session: Annotated[Session, Depends(get_db)], as_of: date | None = None) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
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
    banks = collect_if_lazy(hysa_rates_module.list_banks(history))
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
def ensure_symbol_priced(symbol: str, session: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
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
def get_lots(session: Annotated[Session, Depends(get_db)], as_of: date | None = None) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
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
def get_risk(
    session: Annotated[Session, Depends(get_db)], start: date | None = None, end: date | None = None
) -> dict[str, Any]:
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
    ledger = _load_ledger(session)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return {"max_drawdown_pct": dashboard.risk_stat(ledger, _config(), range_start, range_end)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/data-quality")
def get_data_quality(session: Annotated[Session, Depends(get_db)]) -> list[dict[str, Any]]:
    """Return the last cached price date per symbol ever held or benchmarked against.

    Returns
    -------
    list[dict[str, Any]]
        One entry per symbol.
    """
    config = _config()
    ledger = _load_ledger(session)
    held_and_benchmark = {*ledger["symbol"].unique().to_list(), dashboard.resolved_benchmark_symbol(config)}
    symbols = sorted(held_and_benchmark - {config.ledger.cash_symbol})
    return dashboard.data_quality(symbols, config).to_dicts()


@app.get("/api/ledger/export")
def get_ledger_export(session: Annotated[Session, Depends(get_db)]) -> list[dict[str, Any]]:
    """Export the full ledger, for the user's own backup.

    Returns
    -------
    list[dict[str, Any]]
        Every ledger row.
    """
    return _load_ledger(session).to_dicts()


@app.get("/api/statements/export")
def get_statements_export() -> Response:
    """Zip every raw Flex statement archived from a sync (verbatim XML, as received) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived statement, empty if
        nothing has ever been synced.
    """
    buffer = io.BytesIO()
    archive = StatementArchive(_config().ibkr.raw_statement_dir, f"statements/{DEFAULT_USER_ID}/ibkr")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path, data in archive.read_all():
            zip_file.writestr(relative_path, data)
    filename = f"trades-statements-{datetime.now(tz=UTC).date().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@dataclass
class _SyncStep:
    """One independent leg of a sync — a UI-friendly label, not the underlying provider's name."""

    label: str
    ok: bool
    error: str | None = None


def _run_sync(config: AppConfig, session: Session) -> dict[str, Any]:
    """Run every leg of a sync independently — one failing never skips the rest.

    A bad IBKR token shouldn't also block a benchmark price refresh that
    has nothing to do with IBKR; each leg below is caught on its own, so
    e.g. market prices and savings rates still update even if IBKR itself
    is down. `steps` in the return value reports each leg's own
    success/failure — that's what the UI shows, not just one overall
    pass/fail.

    Returns
    -------
    dict[str, Any]
        `synced_at`, `new_event_count`, `total_event_count`,
        `symbols_refreshed`, and `steps` — each leg's own `label`/`ok`/`error`.
    """
    steps: list[_SyncStep] = []

    _report_sync_progress("Connecting to IBKR", 0.0)
    sync_result = None
    try:
        credentials = resolve_ibkr_credentials(config)
        sync_result = main.sync_ibkr_account(credentials, config, session, on_progress=_report_sync_progress)
        steps.append(_SyncStep("Portfolio data", ok=True))
    except requests.exceptions.RequestException:
        # Not str(error): a request-level failure's own message includes the
        # full IBKR request URL, which embeds the token as a query param (see
        # trades.brokers.ibkr.api._send_flex_request) — must never reach the client.
        steps.append(_SyncStep("Portfolio data", ok=False, error="Could not reach IBKR"))
    except Exception as error:  # noqa: BLE001 — one leg's failure must never abort the rest
        steps.append(_SyncStep("Portfolio data", ok=False, error=str(error)))

    raw_ledger = main.load_ledger(session)
    benchmark_symbol = dashboard.resolved_benchmark_symbol(config)
    today = datetime.now(tz=UTC).date()
    if raw_ledger.is_empty():
        # Nothing's ever been synced successfully — nothing to backfill
        # held-symbol prices for, but the benchmark/CPI/HYSA legs below are
        # still worth attempting on their own.
        held_symbols: list[str] = []
        first_event = today
    else:
        held_symbols = sorted(set(raw_ledger["symbol"].unique().to_list()) - {config.ledger.cash_symbol})
        first_event = _first_event_date(raw_ledger)
    raw_symbols = sorted({*held_symbols, benchmark_symbol})

    _report_sync_progress("Updating price history", 65.0)
    try:
        prices.update_price_caches(raw_symbols, since=first_event, as_of=today, config=config)
        steps.append(_SyncStep("Market prices", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(_SyncStep("Market prices", ok=False, error=str(error)))

    _report_sync_progress("Updating benchmark prices", 80.0)
    try:
        prices.update_price_cache(benchmark_symbol, since=first_event, as_of=today, config=config, adjusted=True)
        steps.append(_SyncStep("Benchmark prices", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(_SyncStep("Benchmark prices", ok=False, error=str(error)))

    _report_sync_progress("Updating CPI index", 90.0)
    try:
        cpi_module.update_cpi_cache(config)
        steps.append(_SyncStep("Inflation data", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(_SyncStep("Inflation data", ok=False, error=str(error)))

    _report_sync_progress("Updating savings rates", 95.0)
    try:
        hysa_rates_module.update_hysa_rates_cache(config)
        steps.append(_SyncStep("Savings rates", ok=True))
    except Exception as error:  # noqa: BLE001
        steps.append(_SyncStep("Savings rates", ok=False, error=str(error)))

    return {
        "synced_at": _last_synced_iso(),
        "new_event_count": sync_result.new_event_count if sync_result else 0,
        "total_event_count": sync_result.total_event_count if sync_result else raw_ledger.height,
        "symbols_refreshed": raw_symbols,
        "steps": [asdict(step) for step in steps],
    }


@app.post("/api/sync")
def sync(session: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """Pull the latest IBKR statement and refresh the price/CPI/HYSA-rate caches.

    Refreshes the raw price cache for every symbol ever held plus the
    benchmark symbol, the adjusted (dividend-reinvested) cache for the
    benchmark symbol only (adjusted prices are for benchmark
    counterfactuals, never for pricing your own positions), the CPI
    cache, and every bank's HYSA rate history. Reports progress to
    `app.state.sync_progress` throughout, readable via `GET
    /api/sync/progress` — the IBKR pull is the one step slow enough that a
    bare spinner isn't good enough feedback.

    Concurrent requests are serialized by a lock to prevent cache and ledger
    corruption from simultaneous writes.

    Returns
    -------
    dict[str, Any]
        `synced_at`, `new_event_count`, `total_event_count`, `symbols_refreshed`,
        and `steps` — each leg's own `label`/`ok`/`error`, since one
        failing (e.g. a bad IBKR token) no longer aborts the rest.
    """
    with _sync_lock:
        try:
            result = _run_sync(_config(), session)
        except Exception as error:
            # Only reachable for something outside every leg's own
            # try/except in _run_sync — each expected failure mode is
            # already caught there and reported per-step instead.
            app.state.sync_progress = SyncProgress(step="Sync failed", percent=100.0, done=True, error=str(error))
            raise

        app.state.sync_progress = SyncProgress(step="Done", percent=100.0, done=True)
        return result


if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        """Serve the built React app for anything no route above matched.

        Registered last on purpose: Starlette matches routes in registration
        order, so every `/api/...` route (and `/docs`, `/openapi.json`)
        defined earlier in this module is tried first. Falls back to
        `index.html` for any path that isn't a real file in `web/dist/` —
        e.g. a hard refresh on `/settings` — so the frontend's client-side
        router gets a chance to handle it instead of a bare 404.

        Returns
        -------
        FileResponse
            The requested static file if it exists under `web/dist/`,
            otherwise `index.html` so client-side routing can take over.
        """
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_relative_to(_FRONTEND_DIST) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
