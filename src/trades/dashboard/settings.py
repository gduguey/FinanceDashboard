"""User-editable dashboard settings and resolved configuration helpers."""

from __future__ import annotations

import uuid
from bisect import bisect_right
from datetime import date
from typing import TYPE_CHECKING, cast

import polars as pl
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, bindparam, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.base import RateMap, ensure_reference_rows
from db.money import Rate
from trades.config import TaxRegime
from trades.db.models import DashboardSettings as DashboardSettingsRow
from trades.db.models import Security
from trades.ledger.taxes import after_tax_rate_lookup
from trades.market_data import hysa_rates as hysa_rates_module

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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

    target_allocation_pct: dict[str, Rate] = Field(default_factory=dict)
    hysa_bank_id: str | None = None
    hysa_fixed_rate_pct: Rate | None = None
    benchmark_symbol_override: str | None = None
    local_zone: str | None = None
    tax_enabled: bool = False
    tax_regime: TaxRegime | None = None
    residency_status_change_date: date | None = None
    w8ben_claimed: bool = False
    w8ben_treaty_rate_pct: Rate | None = None
    marginal_ordinary_rate_pct: Rate | None = None
    qualified_ltcg_rate_pct: Rate | None = None


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


def save_settings(settings: DashboardSettings, session: Session, user_id: uuid.UUID) -> None:
    """Persist this user's dashboard settings, overwriting whatever was saved before.

    Deliberately last-write-wins: no optimistic concurrency at all, no
    version column, no expected-version argument. This is one row per
    user, edited from one settings panel by the one person who owns it,
    and every field on it is an idempotent preference (a bank id, a
    benchmark symbol, a tax rate, a browser-reported timezone) — the only
    thing that matters about the end state is which value was actually
    wanted last, exactly the case
    `docs/optimistic-concurrency-versioning.md` identifies as *not* worth
    version-checking. A version check here bought no protection against
    real lost work and instead made two unrelated saves against the same
    row (a timezone report racing a tax-rate edit) spuriously 409.
    Optimistic concurrency in this repo lives per-row on the accounting
    tables that hold genuinely conflicting user intent — see
    `db.base.check_and_bump_row_version`.

    Parameters
    ----------
    settings
        The settings to persist.
    session
        An active database session.
    user_id
        Whose settings this is.
    """
    row = session.get(DashboardSettingsRow, user_id)
    if row is None:
        row = DashboardSettingsRow(user_id=user_id)
        session.add(row)
    # `benchmark_symbol_override` references `trades.securities` now. The
    # picker's symbols come from a live Yahoo search (`market_data.symbol_search`),
    # so the chosen instrument may well be one this database has never seen —
    # the reference is created here rather than rejecting the save. `None`
    # (no override) is dropped by the helper, not tested for here.
    ensure_reference_rows(session, Security, [settings.benchmark_symbol_override])
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


def merge_target_allocation(session: Session, user_id: uuid.UUID, patch: Mapping[str, Rate | None]) -> dict[str, Rate]:
    """Apply an RFC 7386 merge patch to the target allocation, in the database, in one statement.

    The reason this is not `load_settings` / edit / `save_settings` is that
    the read-modify-write loses a concurrent edit (known gap 4, item A4b).
    Two `PATCH`es naming *different* symbols each read the map, each add
    their own key to the copy they read, and each write the whole map back —
    so the later commit drops the earlier one. That directly contradicts
    what this endpoint's own media type promises: the whole point of
    `application/merge-patch+json` is that a patch says only what changed,
    and patches of disjoint keys therefore compose.

    **Not** a version column, deliberately, and not a row lock either. A
    version would make the two patches conflict and 409 one of them, which
    is a worse answer than merging them — and `save_settings` documents at
    length why this particular row is last-write-wins. A database-side merge
    composes *by construction*: `ON CONFLICT DO UPDATE` re-reads the row it
    is updating, so under READ COMMITTED the second patch's `||` applies to
    the first one's committed value and both survive. Nothing to retry,
    nothing to conflict, no new column.

    `jsonb`'s `||` is a shallow merge and `- text[]` removes keys, which is
    exactly merge-patch over a flat map: a symbol with a number sets it, a
    symbol with an explicit `null` **deletes** it (not "sets it to null" —
    the distinction merge-patch turns on), and a symbol the body never
    mentions is untouched. The map is flat by construction (`db.base.RateMap`
    stores `symbol -> exact decimal string`), so shallow is the whole of it;
    a nested resource would need the recursive form.

    An upsert rather than an update because the settings row is created
    lazily — a user who has never saved a preference has no row, and their
    first patch must still land. Empty patches need no special case: `|| '{}'`
    and `- '{}'` are both no-ops.

    `updated_at` is set explicitly, because a Core statement does not go
    through the mapper that would otherwise apply `Timestamped`'s `onupdate`.

    Parameters
    ----------
    session
        An active database session; committed here.
    user_id
        Whose allocation to patch.
    patch
        Symbol to target percentage, or to `None` to remove that symbol.

    Returns
    -------
    dict[str, Rate]
        The whole resulting allocation, read back from the row that was
        written — not the caller's own merge of it, which is the value that
        could disagree with what another patch just committed.
    """
    assignments = {symbol: target for symbol, target in patch.items() if target is not None}
    removals = sorted(symbol for symbol, target in patch.items() if target is None)

    allocation = DashboardSettingsRow.target_allocation_pct
    # `type_=RateMap` on both binds keeps the Decimal-to-exact-string encoding
    # in the one place that owns it, rather than restating it in SQL here —
    # and makes `RETURNING` decode back to `Decimal` on the way out.
    # `self_group()` is load-bearing, not decoration. Postgres puts binary `-`
    # at a *higher* precedence than `||`, so the unparenthesised
    # `allocation || :assignments - :removals` parses as
    # `allocation || (:assignments - :removals)` — it deletes the keys from
    # the patch instead of from the stored map, and a delete-only patch then
    # silently does nothing at all.
    merged = (
        allocation
        .concat(bindparam("assignments", assignments, type_=RateMap))
        .self_group()
        .op("-")(bindparam("removals", removals, type_=ARRAY(Text)))
    )
    statement = (
        pg_insert(DashboardSettingsRow)
        .values(
            user_id=user_id,
            target_allocation_pct=bindparam("initial", assignments, type_=RateMap),
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={"target_allocation_pct": merged, "updated_at": func.now()},
        )
        .returning(allocation)
    )
    result: dict[str, Rate] = session.execute(statement).scalar_one()
    session.commit()
    return result


def raw_hysa_rate_lookup(config: AppConfig, settings: DashboardSettings) -> Callable[[date], float]:
    """Build the published-rate HYSA lookup, before any after-tax adjustment.

    Priority: an explicit fixed-rate override, then the selected (or
    default) bank's real historical APY, falling back to
    `config.returns.hysa_annual_rate` for any day that bank has no
    published rate for yet (e.g. before its history starts).

    The bank's own rows are read out of the cache once, here, and searched
    per day with a bisect rather than by calling
    `market_data.hysa_rates.rate_as_of` — which filters, sorts and collects
    the whole multi-bank history on every call. Every caller walks a span
    of calendar days one at a time (`ledger.counterfactuals.hysa_counterfactual_series`,
    `dashboard.holdings._hysa_growth_index`), so that per-call cost is paid
    once per *day* in the span: measured at 403 ms over an eight-year span,
    against 3 ms after this change. `rate_as_of` stays the right call for
    a one-off lookup, which is what `_benchmark_apy_pct` makes.

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
            return float(fixed_rate)

        return fixed

    bank_id = settings.hysa_bank_id or config.hysa_rates.default_bank_id
    history = hysa_rates_module.load_hysa_rates_cache(config)
    published = history.filter(pl.col("bank_id") == bank_id).sort("rate_date")
    change_dates: list[date] = published["rate_date"].to_list()
    change_rates: list[float] = published["apy_pct"].to_list()

    def rate(day: date) -> float:
        """Return the chosen bank's published rate on `day`, falling back to the configured default.

        Returns
        -------
        float
        """
        # The last row dated on or before `day` — `rate_as_of`'s own
        # "rates only get a row when they change, so roll back" rule.
        index = bisect_right(change_dates, day)
        apy_pct = change_rates[index - 1] if index else None
        return apy_pct / 100 if apy_pct is not None else config.returns.hysa_annual_rate

    return rate


def hysa_rate_lookup(config: AppConfig, settings: DashboardSettings) -> Callable[[date], float]:
    """Build the HYSA rate lookup every HYSA counterfactual on the dashboard shares.

    Because the overview's excess-value-vs-HYSA card, the closed-lot
    excess-return column, the dollar chart, and the
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
    return float(override / 100) if override is not None else config.tax.marginal_ordinary_rate


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
    return float(override / 100) if override is not None else config.tax.qualified_ltcg_rate


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
        return float(settings.w8ben_treaty_rate_pct / 100)
    return config.tax.nra_statutory_dividend_withholding_rate
