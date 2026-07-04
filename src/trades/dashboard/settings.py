"""User-editable dashboard settings and resolved configuration helpers."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from trades.config import TaxRegime
from trades.ledger.taxes import after_tax_rate_lookup
from trades.market_data import hysa_rates as hysa_rates_module
from trades.utils.io_utils import write_json_atomic

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
    """

    model_config = ConfigDict(frozen=True)

    target_allocation_pct: dict[str, float] = Field(default_factory=dict)
    hysa_bank_id: str | None = None
    hysa_fixed_rate_pct: float | None = None
    benchmark_symbol_override: str | None = None
    tax_enabled: bool = False
    tax_regime: TaxRegime | None = None
    residency_status_change_date: date | None = None
    w8ben_claimed: bool = False
    w8ben_treaty_rate_pct: float | None = None
    marginal_ordinary_rate_pct: float | None = None
    qualified_ltcg_rate_pct: float | None = None


def load_settings(config: AppConfig) -> DashboardSettings:
    """Read the persisted dashboard settings, or the defaults if none have been saved yet.

    Parameters
    ----------
    config
        Application configuration; `config.dashboard.settings_path` is read.

    Returns
    -------
    DashboardSettings
        The persisted settings, or `DashboardSettings()` if `settings_path` doesn't exist yet.
    """
    if not config.dashboard.settings_path.exists():
        return DashboardSettings()
    return DashboardSettings.model_validate_json(config.dashboard.settings_path.read_text())


def save_settings(settings: DashboardSettings, config: AppConfig) -> None:
    """Persist dashboard settings, overwriting whatever was saved before.

    Parameters
    ----------
    settings
        The settings to persist.
    config
        Application configuration; `config.dashboard.settings_path` is written to.
    """
    write_json_atomic(settings.model_dump(mode="json"), config.dashboard.settings_path)


def raw_hysa_rate_lookup(config: AppConfig) -> Callable[[date], float]:
    """Build the published-rate HYSA lookup, before any after-tax adjustment.

    Priority: an explicit fixed-rate override, then the selected (or
    default) bank's real historical APY, falling back to
    `config.returns.hysa_annual_rate` for any day that bank has no
    published rate for yet (e.g. before its history starts).

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    settings = load_settings(config)
    if settings.hysa_fixed_rate_pct is not None:
        fixed_rate = settings.hysa_fixed_rate_pct / 100

        def fixed(_day: date) -> float:
            return fixed_rate

        return fixed

    bank_id = settings.hysa_bank_id or config.hysa_rates.default_bank_id
    history = hysa_rates_module.load_hysa_rates_cache(config)

    def rate(day: date) -> float:
        apy_pct = hysa_rates_module.rate_as_of(history, bank_id, day)
        return apy_pct / 100 if apy_pct is not None else config.returns.hysa_annual_rate

    return rate


def hysa_rate_lookup(config: AppConfig) -> Callable[[date], float]:
    """Build the HYSA rate lookup every HYSA counterfactual on the dashboard shares.

    Because the overview's dollar-alpha card, the dollar chart, and the
    growth-of-$100 chart all source their HYSA leg from this one function,
    turning on `DashboardSettings.tax_enabled` here — wrapping the published
    rate through `taxes.after_tax_rate_lookup` — is enough to make every one
    of them switch from the pre-tax rate to an after-tax one at once,
    without each chart needing its own tax-awareness. Left off, this
    returns the published rate unchanged, exactly as before tax support
    existed.

    Returns
    -------
    Callable[[datetime.date], float]
        The rate (as a fraction, e.g. `0.04`) as of a given date.
    """
    settings = load_settings(config)
    raw_rate = raw_hysa_rate_lookup(config)
    if not settings.tax_enabled:
        return raw_rate
    return after_tax_rate_lookup(
        raw_rate,
        resolved_marginal_ordinary_rate(config),
        resolved_tax_regime(config),
        settings.residency_status_change_date,
    )


def resolved_benchmark_symbol(config: AppConfig) -> str:
    """Resolve the benchmark symbol to use: the user's override if set, else `config.returns.benchmark_symbol`.

    Returns
    -------
    str
        The ticker symbol to benchmark against.
    """
    return load_settings(config).benchmark_symbol_override or config.returns.benchmark_symbol


def resolved_tax_regime(config: AppConfig) -> TaxRegime:
    """Resolve the tax regime to use: the user's selection, or the fully taxed default if never made.

    Returns
    -------
    TaxRegime
        `RESIDENT` unless the user has explicitly selected `NRA` — the
        dashboard never assumes the more favorable nonresident-alien
        treatment on the user's behalf.
    """
    return load_settings(config).tax_regime or "RESIDENT"


def resolved_marginal_ordinary_rate(config: AppConfig) -> float:
    """Resolve the ordinary-income tax rate to use: the user's override, or the code default.

    Returns
    -------
    float
        `config.tax.marginal_ordinary_rate` unless the user has entered their own rate.
    """
    override = load_settings(config).marginal_ordinary_rate_pct
    return override / 100 if override is not None else config.tax.marginal_ordinary_rate


def resolved_qualified_ltcg_rate(config: AppConfig) -> float:
    """Resolve the long-term-capital-gains/qualified-dividend rate to use: the user's override, or the code default.

    Returns
    -------
    float
        `config.tax.qualified_ltcg_rate` unless the user has entered their own rate.
    """
    override = load_settings(config).qualified_ltcg_rate_pct
    return override / 100 if override is not None else config.tax.qualified_ltcg_rate


def resolved_nra_dividend_tax_rate(config: AppConfig) -> float:
    """Resolve the flat rate a nonresident alien's dividends are taxed at.

    A tax treaty only lowers the rate below the default statutory
    withholding rate if it has actually been claimed (IRS Form W-8BEN) and
    a specific negotiated rate has been entered; claiming it without
    giving a rate is treated the same as not claiming it at all, since the
    statutory rate is the safe assumption absent a known number.

    Returns
    -------
    float
        The claimed treaty rate, or `config.tax.nra_statutory_dividend_withholding_rate`.
    """
    settings = load_settings(config)
    if settings.w8ben_claimed and settings.w8ben_treaty_rate_pct is not None:
        return settings.w8ben_treaty_rate_pct / 100
    return config.tax.nra_statutory_dividend_withholding_rate
