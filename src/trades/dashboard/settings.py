"""User-editable dashboard settings and resolved configuration helpers."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from db.base import check_and_bump_version, get_version
from trades.config import TaxRegime
from trades.db.models import DashboardSettings as DashboardSettingsRow
from trades.ledger.taxes import after_tax_rate_lookup
from trades.market_data import hysa_rates as hysa_rates_module

_DASHBOARD_SETTINGS_VERSION_TABLE = "trades.dashboard_settings_versions"

if TYPE_CHECKING:
    from collections.abc import Callable

    from trades.config import AppConfig


class DashboardSettings(BaseModel):
    """User-editable dashboard settings, persisted outside the ledger.

    `hysa_fixed_rate_pct` takes priority over `hysa_bank_id` when both are
    set — an explicit fixed rate is a deliberate override, not just a
    fallback. `hysa_bank_id`/`benchmark_symbol_override` being unset falls
    back to `config.hysa_rates.default_bank_id`/`config.returns.benchmark_symbol`.
    `tax_regime` left unset falls back to `RESIDENT`, the fully taxed
    baseline, rather than assuming the more favorable nonresident-alien
    treatment on the user's behalf; `residency_status_change_date` left
    unset means `tax_regime` has applied to the whole account history.
    `marginal_ordinary_rate_pct`/`qualified_ltcg_rate_pct` left unset fall
    back to `config.tax.marginal_ordinary_rate`/`config.tax.qualified_ltcg_rate`.
    `w8ben_treaty_rate_pct` only means anything when `tax_regime` is `NRA`
    and `w8ben_claimed` is set — it does not change any historical figure
    (real withholding already happened at whatever rate the broker
    actually applied); it only feeds the forward-looking tax-owed estimate.
    `local_zone` left unset falls back to `config.timezone.local_zone` —
    normally never unset for long, since the frontend reports the
    browser's own IANA zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`)
    the first time it loads, the same way it's the source of truth for
    every other per-user preference here.
    """

    model_config = ConfigDict(frozen=True)

    target_allocation_pct: dict[str, float] = Field(default_factory=dict)
    hysa_bank_id: str | None = None
    hysa_fixed_rate_pct: float | None = None
    benchmark_symbol_override: str | None = None
    local_zone: str | None = None
    tax_enabled: bool = False
    tax_regime: TaxRegime | None = None
    residency_status_change_date: date | None = None
    w8ben_claimed: bool = False
    w8ben_treaty_rate_pct: float | None = None
    marginal_ordinary_rate_pct: float | None = None
    qualified_ltcg_rate_pct: float | None = None


def load_settings(session: Session, user_id: uuid.UUID) -> DashboardSettings:
    """Read this user's persisted dashboard settings, or the defaults if none have been saved yet.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose settings to read.

    Returns
    -------
    DashboardSettings
    """
    row = session.get(DashboardSettingsRow, user_id)
    if row is None:
        return DashboardSettings()
    return DashboardSettings(
        target_allocation_pct=row.target_allocation_pct,
        hysa_bank_id=row.hysa_bank_id,
        hysa_fixed_rate_pct=row.hysa_fixed_rate_pct,
        benchmark_symbol_override=row.benchmark_symbol_override,
        local_zone=row.local_zone,
        tax_enabled=row.tax_enabled,
        tax_regime=cast("TaxRegime | None", row.tax_regime),
        residency_status_change_date=row.residency_status_change_date,
        w8ben_claimed=row.w8ben_claimed,
        w8ben_treaty_rate_pct=row.w8ben_treaty_rate_pct,
        marginal_ordinary_rate_pct=row.marginal_ordinary_rate_pct,
        qualified_ltcg_rate_pct=row.qualified_ltcg_rate_pct,
    )


def get_dashboard_settings_version(session: Session, user_id: uuid.UUID) -> int:
    """Read this user's current dashboard-settings save-version counter.

    Parameters
    ----------
    session
        An active database session.
    user_id
        Whose counter to read.

    Returns
    -------
    int
        `0` if this user has never saved settings yet (no row exists).
    """
    return get_version(session, _DASHBOARD_SETTINGS_VERSION_TABLE, user_id)


def save_settings(settings: DashboardSettings, session: Session, user_id: uuid.UUID) -> None:
    """Persist this user's dashboard settings, overwriting whatever was saved before.

    Reads an expected version from `session.info["expected_dashboard_settings_version"]`
    — stashed once per request by `trades.api.dependencies`'s
    `_stash_expected_dashboard_settings_version`, from the client's own
    `X-Expected-Dashboard-Settings-Version` header — the same optimistic-
    concurrency mechanism `accounting.store.save_store` uses (see
    `db.base.check_and_bump_version`), since every one of this module's
    five settings endpoints reads-modifies-writes the same one shared row.

    Parameters
    ----------
    settings
        The settings to persist.
    session
        An active database session.
    user_id
        Whose settings this is.
    """
    expected_version = session.info.get("expected_dashboard_settings_version")
    check_and_bump_version(session, _DASHBOARD_SETTINGS_VERSION_TABLE, user_id, expected_version)

    row = session.get(DashboardSettingsRow, user_id)
    if row is None:
        row = DashboardSettingsRow(user_id=user_id)
        session.add(row)
    row.target_allocation_pct = settings.target_allocation_pct
    row.hysa_bank_id = settings.hysa_bank_id
    row.hysa_fixed_rate_pct = settings.hysa_fixed_rate_pct
    row.benchmark_symbol_override = settings.benchmark_symbol_override
    row.local_zone = settings.local_zone
    row.tax_enabled = settings.tax_enabled
    row.tax_regime = settings.tax_regime
    row.residency_status_change_date = settings.residency_status_change_date
    row.w8ben_claimed = settings.w8ben_claimed
    row.w8ben_treaty_rate_pct = settings.w8ben_treaty_rate_pct
    row.marginal_ordinary_rate_pct = settings.marginal_ordinary_rate_pct
    row.qualified_ltcg_rate_pct = settings.qualified_ltcg_rate_pct
    session.commit()


def raw_hysa_rate_lookup(config: AppConfig, settings: DashboardSettings) -> Callable[[date], float]:
    """Build the published-rate HYSA lookup, before any after-tax adjustment.

    Priority: an explicit fixed-rate override, then the selected (or
    default) bank's real historical APY, falling back to
    `config.returns.hysa_annual_rate` for any day that bank has no
    published rate for yet (e.g. before its history starts).

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    if settings.hysa_fixed_rate_pct is not None:
        fixed_rate = settings.hysa_fixed_rate_pct / 100

        def fixed(_day: date) -> float:
            """Return the user's configured fixed override rate.

            Returns
            -------
            float
            """
            return fixed_rate

        return fixed

    bank_id = settings.hysa_bank_id or config.hysa_rates.default_bank_id
    history = hysa_rates_module.load_hysa_rates_cache(config)

    def rate(day: date) -> float:
        """Return the chosen bank's published rate on `day`, falling back to the configured default.

        Returns
        -------
        float
        """
        apy_pct = hysa_rates_module.rate_as_of(history, bank_id, day)
        return apy_pct / 100 if apy_pct is not None else config.returns.hysa_annual_rate

    return rate


def hysa_rate_lookup(config: AppConfig, settings: DashboardSettings) -> Callable[[date], float]:
    """Build the HYSA rate lookup every HYSA counterfactual on the dashboard shares.

    Because the overview's dollar-alpha card, the dollar chart, and the
    growth-of-$100 chart all source their HYSA leg from this one function,
    turning on `DashboardSettings.tax_enabled` here — wrapping the published
    rate through `taxes.after_tax_rate_lookup` — is enough to make every one
    of them switch from the pre-tax rate to an after-tax one at once,
    without each chart needing its own tax-awareness. Left off, this
    returns the published rate unchanged, exactly as before tax support
    existed.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    raw_rate = raw_hysa_rate_lookup(config, settings)
    if not settings.tax_enabled:
        return raw_rate
    return after_tax_rate_lookup(
        raw_rate,
        resolved_marginal_ordinary_rate(config, settings),
        resolved_tax_regime(settings),
        settings.residency_status_change_date,
    )


def resolved_benchmark_symbol(config: AppConfig, settings: DashboardSettings) -> str:
    """Resolve the benchmark symbol to use: the user's override if set, else `config.returns.benchmark_symbol`.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    str
        The ticker symbol to benchmark against.
    """
    return settings.benchmark_symbol_override or config.returns.benchmark_symbol


def resolved_local_zone(config: AppConfig, settings: DashboardSettings) -> str:
    """Resolve the display timezone to use: the browser-reported zone, or `config.timezone.local_zone`.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    str
        An IANA zone name (e.g. `"America/New_York"`).
    """
    return settings.local_zone or config.timezone.local_zone


def resolved_tax_regime(settings: DashboardSettings) -> TaxRegime:
    """Resolve the tax regime to use: the user's selection, or the fully taxed default if never made.

    Parameters
    ----------
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    TaxRegime
        `RESIDENT` unless the user has explicitly selected `NRA` — the
        dashboard never assumes the more favorable nonresident-alien
        treatment on the user's behalf.
    """
    return settings.tax_regime or "RESIDENT"


def resolved_marginal_ordinary_rate(config: AppConfig, settings: DashboardSettings) -> float:
    """Resolve the ordinary-income tax rate to use: the user's override, or the code default.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    float
        `config.tax.marginal_ordinary_rate` unless the user has entered their own rate.
    """
    override = settings.marginal_ordinary_rate_pct
    return override / 100 if override is not None else config.tax.marginal_ordinary_rate


def resolved_qualified_ltcg_rate(config: AppConfig, settings: DashboardSettings) -> float:
    """Resolve the long-term-capital-gains/qualified-dividend rate to use: the user's override, or the code default.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    float
        `config.tax.qualified_ltcg_rate` unless the user has entered their own rate.
    """
    override = settings.qualified_ltcg_rate_pct
    return override / 100 if override is not None else config.tax.qualified_ltcg_rate


def resolved_nra_dividend_tax_rate(config: AppConfig, settings: DashboardSettings) -> float:
    """Resolve the flat rate a nonresident alien's dividends are taxed at.

    A tax treaty only lowers the rate below the default statutory
    withholding rate if it has actually been claimed (IRS Form W-8BEN) and
    a specific negotiated rate has been entered; claiming it without
    giving a rate is treated the same as not claiming it at all, since the
    statutory rate is the safe assumption absent a known number.

    Parameters
    ----------
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.

    Returns
    -------
    float
        The claimed treaty rate, or `config.tax.nra_statutory_dividend_withholding_rate`.
    """
    if settings.w8ben_claimed and settings.w8ben_treaty_rate_pct is not None:
        return settings.w8ben_treaty_rate_pct / 100
    return config.tax.nra_statutory_dividend_withholding_rate
