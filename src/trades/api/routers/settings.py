"""Settings endpoints — mirrors `trades.config`/`trades.brokers.ibkr.credentials`: allocation, HYSA, tax, IBKR."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db
from trades import dashboard
from trades.api.api_models import (
    BenchmarkSetting,
    BenchmarkSettingUpdate,
    HysaSettings,
    HysaSettingsUpdate,
    IbkrCredentialsUpdate,
    IbkrSettings,
    TargetAllocationSetting,
    TaxSettings,
    TaxSettingsUpdate,
    TimezoneSetting,
    TimezoneSettingUpdate,
    VerifyResult,
)
from trades.api.dependencies import _config, _stash_expected_dashboard_settings_version
from trades.brokers.ibkr import api as ibkr_api
from trades.brokers.ibkr.credentials import (
    IbkrCredentialsNotConfiguredError,
    clear_ibkr_credentials,
    ibkr_credential_fields,
    ibkr_is_configured,
    resolve_ibkr_credentials,
    save_ibkr_credentials,
)
from trades.config import AppConfig
from trades.dashboard.settings import get_dashboard_settings_version

# Runs for every endpoint in this router, IBKR-credential ones included —
# harmless there (it just stashes a value nothing reads back), so
# `save_settings`'s version check works for all five `DashboardSettings`
# endpoints without each needing its own copy of this dependency.
router = APIRouter(dependencies=[Depends(_stash_expected_dashboard_settings_version)])


@router.get("/api/settings/target-allocation")
def get_target_allocation(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TargetAllocationSetting:
    """Return the persisted target allocation, with the settings-row version.

    Returns
    -------
    TargetAllocationSetting
        `target_allocation_pct` (symbol -> target percentage) and `version`.
    """
    return TargetAllocationSetting(
        target_allocation_pct=dashboard.load_settings(session, user_id).target_allocation_pct,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.put("/api/settings/target-allocation")
def put_target_allocation(
    target_allocation_pct: dict[str, float],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TargetAllocationSetting:
    """Persist a new target allocation, set from the frontend.

    Merges into the existing settings — a settings row is one record, so
    writing this field naively from a fresh `DashboardSettings()` would
    silently wipe out the HYSA/benchmark settings saved separately. The
    response carries `version` (like every other settings endpoint) so the
    client's cached version stays current and a follow-up save to another
    settings field doesn't spuriously 409.

    Returns
    -------
    TargetAllocationSetting
        The persisted target allocation and the new version.
    """
    updated = dashboard.load_settings(session, user_id).model_copy(
        update={"target_allocation_pct": target_allocation_pct}
    )
    dashboard.save_settings(updated, session, user_id)
    return TargetAllocationSetting(
        target_allocation_pct=updated.target_allocation_pct,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.get("/api/settings/hysa")
def get_hysa_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> HysaSettings:
    """Return the persisted HYSA bank selection / fixed-rate override.

    Returns
    -------
    HysaSettings
        `bank_id`, `fixed_rate_pct` — both None if never set.
    """
    settings = dashboard.load_settings(session, user_id)
    return HysaSettings(
        bank_id=settings.hysa_bank_id,
        fixed_rate_pct=settings.hysa_fixed_rate_pct,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.put("/api/settings/hysa")
def put_hysa_settings(
    update: HysaSettingsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> HysaSettings:
    """Persist a HYSA bank selection and/or fixed-rate override (merges into existing settings).

    Returns
    -------
    HysaSettings
        `bank_id`, `fixed_rate_pct` as persisted.
    """
    updated = dashboard.load_settings(session, user_id).model_copy(
        update={"hysa_bank_id": update.bank_id, "hysa_fixed_rate_pct": update.fixed_rate_pct}
    )
    dashboard.save_settings(updated, session, user_id)
    return HysaSettings(
        bank_id=updated.hysa_bank_id,
        fixed_rate_pct=updated.hysa_fixed_rate_pct,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.get("/api/settings/benchmark")
def get_benchmark_setting(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> BenchmarkSetting:
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
        symbol_override=dashboard.load_settings(session, user_id).benchmark_symbol_override,
        default_symbol=config.returns.benchmark_symbol,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.put("/api/settings/benchmark")
def put_benchmark_setting(
    update: BenchmarkSettingUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> BenchmarkSetting:
    """Persist a benchmark symbol override (merges into existing settings).

    Returns
    -------
    BenchmarkSetting
        `symbol_override` as persisted, plus `default_symbol`.
    """
    config = _config()
    updated = dashboard.load_settings(session, user_id).model_copy(
        update={"benchmark_symbol_override": update.symbol_override}
    )
    dashboard.save_settings(updated, session, user_id)
    return BenchmarkSetting(
        symbol_override=updated.benchmark_symbol_override,
        default_symbol=config.returns.benchmark_symbol,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.get("/api/settings/timezone")
def get_timezone_setting(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TimezoneSetting:
    """Return the persisted display-timezone override, plus what it resolves to.

    Returns
    -------
    TimezoneSetting
        `local_zone` (None if the browser has never reported one for this
        user yet), `resolved_local_zone` (what timestamps actually display
        in — `config.timezone.local_zone` until it has).
    """
    settings = dashboard.load_settings(session, user_id)
    return TimezoneSetting(
        local_zone=settings.local_zone,
        resolved_local_zone=dashboard.resolved_local_zone(_config(), settings),
        version=get_dashboard_settings_version(session, user_id),
    )


@router.put("/api/settings/timezone")
def put_timezone_setting(
    update: TimezoneSettingUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TimezoneSetting:
    """Persist the browser-reported display timezone (merges into existing settings).

    Called by the frontend once per session with
    `Intl.DateTimeFormat().resolvedOptions().timeZone` — never user-picked
    from a list.

    Returns
    -------
    TimezoneSetting
        Same shape as `GET /api/settings/timezone`, reflecting what was
        just persisted.
    """
    updated = dashboard.load_settings(session, user_id).model_copy(update={"local_zone": update.local_zone})
    dashboard.save_settings(updated, session, user_id)
    return TimezoneSetting(
        local_zone=updated.local_zone,
        resolved_local_zone=dashboard.resolved_local_zone(_config(), updated),
        version=get_dashboard_settings_version(session, user_id),
    )


def _tax_settings_response(
    config: AppConfig, settings: dashboard.DashboardSettings, session: Session, user_id: uuid.UUID
) -> TaxSettings:
    """Build the tax-settings API response, resolving the effective regime alongside the raw saved fields.

    Returns
    -------
    TaxSettings
    """
    return TaxSettings(
        tax_enabled=settings.tax_enabled,
        tax_regime=settings.tax_regime,
        resolved_tax_regime=dashboard.resolved_tax_regime(settings),
        residency_status_change_date=settings.residency_status_change_date,
        w8ben_claimed=settings.w8ben_claimed,
        w8ben_treaty_rate_pct=settings.w8ben_treaty_rate_pct,
        marginal_ordinary_rate_pct=settings.marginal_ordinary_rate_pct,
        resolved_marginal_ordinary_rate_pct=dashboard.resolved_marginal_ordinary_rate(config, settings) * 100,
        qualified_ltcg_rate_pct=settings.qualified_ltcg_rate_pct,
        resolved_qualified_ltcg_rate_pct=dashboard.resolved_qualified_ltcg_rate(config, settings) * 100,
        version=get_dashboard_settings_version(session, user_id),
    )


@router.get("/api/settings/tax")
def get_tax_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TaxSettings:
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
    settings = dashboard.load_settings(session, user_id)
    return _tax_settings_response(_config(), settings, session, user_id)


@router.put("/api/settings/tax")
def put_tax_settings(
    update: TaxSettingsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TaxSettings:
    """Persist tax-reporting settings (merges into existing settings).

    Returns
    -------
    TaxSettings
        Same shape as `GET /api/settings/tax`, reflecting what was just persisted.
    """
    config = _config()
    updated = dashboard.load_settings(session, user_id).model_copy(
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
    dashboard.save_settings(updated, session, user_id)
    return _tax_settings_response(config, updated, session, user_id)


@router.get("/api/settings/ibkr")
def get_ibkr_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> IbkrSettings:
    """Report whether IBKR credentials are available, without ever exposing their value.

    Returns
    -------
    IbkrSettings
        `configured` (true once both a token and a query id are saved for
        this user in Postgres — there is no `.env` fallback), `token_set`
        and `query_id_set` (whether each field individually is saved).
    """
    fields = ibkr_credential_fields(session, user_id)
    return IbkrSettings(
        configured=ibkr_is_configured(session, user_id),
        token_set="token" in fields,
        query_id_set="query_id" in fields,
    )


@router.put("/api/settings/ibkr")
def put_ibkr_settings(
    update: IbkrCredentialsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> IbkrSettings:
    """Persist an IBKR credential update (merges into whatever's already saved).

    Returns
    -------
    IbkrSettings
        Same shape as `GET /api/settings/ibkr`, reflecting what was just persisted.
    """
    save_ibkr_credentials(session, user_id, token=update.token, query_id=update.query_id)
    fields = ibkr_credential_fields(session, user_id)
    return IbkrSettings(
        configured=ibkr_is_configured(session, user_id),
        token_set="token" in fields,
        query_id_set="query_id" in fields,
    )


@router.delete("/api/settings/ibkr")
def delete_ibkr_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> IbkrSettings:
    """Clear this user's saved IBKR credentials entirely.

    Returns
    -------
    IbkrSettings
        Same shape as `GET /api/settings/ibkr`.
    """
    clear_ibkr_credentials(session, user_id)
    return IbkrSettings(configured=False, token_set=False, query_id_set=False)


@router.post("/api/settings/ibkr/verify")
def verify_ibkr_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> VerifyResult:
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
        credentials = resolve_ibkr_credentials(session, user_id)
    except IbkrCredentialsNotConfiguredError:
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
