"""Settings endpoints — mirrors `trades.config`/`trades.credentials`: target allocation, HYSA, benchmark, tax, IBKR."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import ValidationError

from trades import dashboard
from trades.api.api_models import (
    BenchmarkSetting,
    BenchmarkSettingUpdate,
    HysaSettings,
    HysaSettingsUpdate,
    IbkrCredentialsUpdate,
    IbkrSettings,
    TaxSettings,
    TaxSettingsUpdate,
    VerifyResult,
)
from trades.api.dependencies import _config
from trades.brokers.ibkr import api as ibkr_api
from trades.config import AppConfig
from trades.credentials import (
    IbkrCredentialOverride,
    ibkr_is_configured,
    load_ibkr_credential_override,
    resolve_ibkr_credentials,
    save_ibkr_credential_override,
)

router = APIRouter()


@router.get("/api/settings/target-allocation")
def get_target_allocation() -> dict[str, float]:
    """Return the persisted target allocation.

    Returns
    -------
    dict[str, float]
        Symbol -> target percentage.
    """
    return dashboard.load_settings(_config()).target_allocation_pct


@router.put("/api/settings/target-allocation")
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


@router.get("/api/settings/hysa")
def get_hysa_settings() -> HysaSettings:
    """Return the persisted HYSA bank selection / fixed-rate override.

    Returns
    -------
    HysaSettings
        `bank_id`, `fixed_rate_pct` — both None if never set.
    """
    settings = dashboard.load_settings(_config())
    return HysaSettings(bank_id=settings.hysa_bank_id, fixed_rate_pct=settings.hysa_fixed_rate_pct)


@router.put("/api/settings/hysa")
def put_hysa_settings(update: HysaSettingsUpdate) -> HysaSettings:
    """Persist a HYSA bank selection and/or fixed-rate override (merges into existing settings).

    Returns
    -------
    HysaSettings
        `bank_id`, `fixed_rate_pct` as persisted.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(
        update={"hysa_bank_id": update.bank_id, "hysa_fixed_rate_pct": update.fixed_rate_pct}
    )
    dashboard.save_settings(updated, config)
    return HysaSettings(bank_id=updated.hysa_bank_id, fixed_rate_pct=updated.hysa_fixed_rate_pct)


@router.get("/api/settings/benchmark")
def get_benchmark_setting() -> BenchmarkSetting:
    """Return the persisted benchmark symbol override, plus the default it falls back to.

    Returns
    -------
    BenchmarkSetting
        `symbol_override` (None if never set) and `default_symbol` — the
        frontend needs the latter to display a concrete symbol even when
        no override is set.
    """
    config = _config()
    return BenchmarkSetting(
        symbol_override=dashboard.load_settings(config).benchmark_symbol_override,
        default_symbol=config.returns.benchmark_symbol,
    )


@router.put("/api/settings/benchmark")
def put_benchmark_setting(update: BenchmarkSettingUpdate) -> BenchmarkSetting:
    """Persist a benchmark symbol override (merges into existing settings).

    Returns
    -------
    BenchmarkSetting
        `symbol_override` as persisted, plus `default_symbol`.
    """
    config = _config()
    updated = dashboard.load_settings(config).model_copy(update={"benchmark_symbol_override": update.symbol_override})
    dashboard.save_settings(updated, config)
    return BenchmarkSetting(
        symbol_override=updated.benchmark_symbol_override, default_symbol=config.returns.benchmark_symbol
    )


def _tax_settings_response(config: AppConfig) -> TaxSettings:
    settings = dashboard.load_settings(config)
    return TaxSettings(
        tax_enabled=settings.tax_enabled,
        tax_regime=settings.tax_regime,
        resolved_tax_regime=dashboard.resolved_tax_regime(config),
        residency_status_change_date=settings.residency_status_change_date,
        w8ben_claimed=settings.w8ben_claimed,
        w8ben_treaty_rate_pct=settings.w8ben_treaty_rate_pct,
        marginal_ordinary_rate_pct=settings.marginal_ordinary_rate_pct,
        resolved_marginal_ordinary_rate_pct=dashboard.resolved_marginal_ordinary_rate(config) * 100,
        qualified_ltcg_rate_pct=settings.qualified_ltcg_rate_pct,
        resolved_qualified_ltcg_rate_pct=dashboard.resolved_qualified_ltcg_rate(config) * 100,
    )


@router.get("/api/settings/tax")
def get_tax_settings() -> TaxSettings:
    """Return the persisted tax-reporting settings.

    Returns
    -------
    TaxSettings
        `tax_enabled`, `tax_regime` (the raw selection, None if never
        set), `resolved_tax_regime` (what the tax report actually uses —
        `RESIDENT` when `tax_regime` is unset), `residency_status_change_date`,
        `w8ben_claimed`, `w8ben_treaty_rate_pct`, `marginal_ordinary_rate_pct`
        and `qualified_ltcg_rate_pct` (the raw overrides, None if never set)
        alongside their `resolved_*_pct` counterparts (what the tax report
        actually uses — the code default when no override was made).
    """
    return _tax_settings_response(_config())


@router.put("/api/settings/tax")
def put_tax_settings(update: TaxSettingsUpdate) -> TaxSettings:
    """Persist tax-reporting settings (merges into existing settings).

    Returns
    -------
    TaxSettings
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


@router.get("/api/settings/ibkr")
def get_ibkr_settings() -> IbkrSettings:
    """Report whether IBKR credentials are available, without ever exposing their value.

    Returns
    -------
    IbkrSettings
        `configured` (true if a token and query id are available from
        either the Settings-page override or `.env`), `token_set` and
        `query_id_set` (whether the Settings-page override itself has
        each field, regardless of `.env`).
    """
    config = _config()
    override = load_ibkr_credential_override(config)
    return IbkrSettings(
        configured=ibkr_is_configured(config),
        token_set=bool(override.token),
        query_id_set=bool(override.query_id),
    )


@router.put("/api/settings/ibkr")
def put_ibkr_settings(update: IbkrCredentialsUpdate) -> IbkrSettings:
    """Persist an IBKR credential override (merges into the existing one).

    Returns
    -------
    IbkrSettings
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
    return IbkrSettings(
        configured=ibkr_is_configured(config),
        token_set=bool(updated.token),
        query_id_set=bool(updated.query_id),
    )


@router.delete("/api/settings/ibkr")
def delete_ibkr_settings() -> IbkrSettings:
    """Clear the Settings-page IBKR credential override, falling back to `.env` (if any) again.

    Returns
    -------
    IbkrSettings
        Same shape as `GET /api/settings/ibkr`.
    """
    config = _config()
    save_ibkr_credential_override(IbkrCredentialOverride(), config)
    return IbkrSettings(configured=ibkr_is_configured(config), token_set=False, query_id_set=False)


@router.post("/api/settings/ibkr/verify")
def verify_ibkr_settings() -> VerifyResult:
    """Actually attempt to authenticate with IBKR, not just check that something's typed in.

    A single fast HTTP call (see `verify_flex_credentials`) — not a full
    sync — so this is cheap enough for the Settings page to call whenever
    it wants a real "does this work" answer instead of "is this set".

    Returns
    -------
    VerifyResult
        `ok` (whether IBKR accepted the token/query id) and `error`
        (IBKR's own message, or a generic one, only when `ok` is false).
    """
    config = _config()
    try:
        credentials = resolve_ibkr_credentials(config)
    except ValidationError:
        return VerifyResult(ok=False, error="No credentials configured")
    try:
        ibkr_api.verify_flex_credentials(credentials, config)
    except ibkr_api.FlexApiError as error:
        return VerifyResult(ok=False, error=error.message)
    except Exception:  # noqa: BLE001 — surfacing any failure to the caller is the entire point here
        # Not str(error): a connection/HTTP error's own message includes the
        # full request URL, which embeds the token as a query param (see
        # `_send_flex_request`) — that must never round-trip back to the client.
        return VerifyResult(ok=False, error="Could not reach IBKR to verify credentials")
    return VerifyResult(ok=True, error=None)
