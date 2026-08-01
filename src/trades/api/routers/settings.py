"""Settings endpoints — mirrors `trades.config`/`trades.brokers.ibkr.credentials`: allocation, HYSA, tax, IBKR.

Every route here is a user preference under `/settings/...`, so the file's
contents and its URL prefix say the same thing. `GET /broker-connections`
used to live here and did not — it is a collection of rows a sync creates,
not something a user sets, and now has its own `broker_connections.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.money import Rate, quantize_rate
from db.session import get_db
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
    TimezoneSetting,
    TimezoneSettingUpdate,
    VerifyResult,
)
from trades.api.dependencies import _config
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

router = APIRouter()


@router.get("/settings/target-allocation")
def get_target_allocation(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Rate]:
    """Return the persisted target allocation.

    Returns
    -------
    dict[str, Rate]
        Symbol -> target percentage.
    """
    return dashboard.load_settings(session, user_id).target_allocation_pct


@router.patch("/settings/target-allocation")
def patch_target_allocation(
    # `Rate`, not `float`, and the reason moved with the merge. It used to be
    # that `model_copy(update=...)` skipped validation, so a `float` would have
    # left the frozen `DashboardSettings` holding a double; there is no
    # `model_copy` here any more. What matters now is that the value goes
    # straight into a `jsonb` column through `db.base.RateMap`, which serialises
    # each rate to its exact decimal string — a `float` would arrive already
    # rounded to the nearest double and be stored exactly that way. `Rate` pins
    # its own OpenAPI type to `number`, so the wire contract is unchanged.
    patch: Annotated[dict[str, Rate | None], Body(media_type="application/merge-patch+json")],
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, Rate]:
    """Apply an RFC 7386 merge patch to the target allocation, one symbol at a time.

    The resource is a `symbol -> target percentage` map, which is exactly
    the shape merge-patch is defined over, so the body says only what
    changed: a symbol with a number sets or replaces that symbol's target,
    a symbol with `null` drops it from the allocation, and a symbol the
    body never mentions is left alone. A caller editing one target no
    longer has to resend every other one and risk clobbering an edit made
    elsewhere in between.

    The merge happens **in the database**, in one statement, rather than by
    reading the map here and writing it back. That is not an optimisation:
    read-modify-write in this handler meant two patches of different symbols
    did not compose — the later commit dropped the earlier one, which is the
    one thing merge-patch is defined not to do (A4b). See
    `dashboard.merge_target_allocation` for the statement and for why this
    row still has no version column.

    It also cannot disturb the rest of the record. The old path rewrote all
    twelve columns of a one-row-per-user settings table via `save_settings`,
    so a concurrent HYSA or timezone save was collateral; the statement now
    touches one `jsonb` column.

    Returns
    -------
    dict[str, Rate]
        The whole resulting allocation, not just the patched entries — read
        back from the row that was written, so it reflects any concurrent
        patch that composed with this one.
    """
    return dashboard.merge_target_allocation(session, user_id, patch)


@router.get("/settings/hysa")
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
    )


@router.put("/settings/hysa")
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
    )


@router.get("/settings/benchmark")
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
    )


@router.put("/settings/benchmark")
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
    )


@router.get("/settings/timezone")
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
    )


@router.put("/settings/timezone")
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
        Same shape as `GET /api/v1/trades/settings/timezone`, reflecting what was
        just persisted.
    """
    updated = dashboard.load_settings(session, user_id).model_copy(update={"local_zone": update.local_zone})
    dashboard.save_settings(updated, session, user_id)
    return TimezoneSetting(
        local_zone=updated.local_zone,
        resolved_local_zone=dashboard.resolved_local_zone(_config(), updated),
    )


def _tax_settings_response(config: AppConfig, settings: dashboard.DashboardSettings) -> TaxSettings:
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
        resolved_marginal_ordinary_rate_pct=quantize_rate(
            dashboard.resolved_marginal_ordinary_rate(config, settings) * 100
        ),
        qualified_ltcg_rate_pct=settings.qualified_ltcg_rate_pct,
        resolved_qualified_ltcg_rate_pct=quantize_rate(dashboard.resolved_qualified_ltcg_rate(config, settings) * 100),
    )


@router.get("/settings/tax")
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
    return _tax_settings_response(_config(), settings)


@router.put("/settings/tax")
def put_tax_settings(
    update: TaxSettingsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TaxSettings:
    """Persist tax-reporting settings (merges into existing settings).

    Returns
    -------
    TaxSettings
        Same shape as `GET /api/v1/trades/settings/tax`, reflecting what was just persisted.
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
    return _tax_settings_response(config, updated)


@router.get("/settings/ibkr")
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


@router.put("/settings/ibkr")
def put_ibkr_settings(
    update: IbkrCredentialsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> IbkrSettings:
    """Persist an IBKR credential update (merges into whatever's already saved).

    Returns
    -------
    IbkrSettings
        Same shape as `GET /api/v1/trades/settings/ibkr`, reflecting what was just persisted.
    """
    save_ibkr_credentials(session, user_id, token=update.token, query_id=update.query_id)
    fields = ibkr_credential_fields(session, user_id)
    return IbkrSettings(
        configured=ibkr_is_configured(session, user_id),
        token_set="token" in fields,
        query_id_set="query_id" in fields,
    )


@router.delete("/settings/ibkr")
def delete_ibkr_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> IbkrSettings:
    """Clear this user's saved IBKR credentials entirely.

    Answers 200 with a body rather than 204 for the same reason
    `DELETE /api/v1/accounting/settings/llm` does: this clears fields on a
    settings row that still exists afterwards, so there is a representation
    to return.

    Returns
    -------
    IbkrSettings
        Same shape as `GET /api/v1/trades/settings/ibkr`.
    """
    clear_ibkr_credentials(session, user_id)
    return IbkrSettings(configured=False, token_set=False, query_id_set=False)


@router.post("/settings/ibkr/verify")
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
