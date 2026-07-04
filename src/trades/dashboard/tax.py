"""Tax summary and liquidation estimates for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.dashboard.settings import (
    load_settings,
    raw_hysa_rate_lookup,
    resolved_marginal_ordinary_rate,
    resolved_nra_dividend_tax_rate,
    resolved_qualified_ltcg_rate,
    resolved_tax_regime,
)
from trades.dashboard.valuation import make_price_lookup
from trades.ledger.counterfactuals import hysa_counterfactual_value
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger
from trades.ledger.taxes import (
    after_tax_rate_lookup,
    annual_tax_report,
    flag_wash_sales,
    liquidation_gain_buckets,
    liquidation_tax_usd,
    preview_sale,
    tax_owed_by_year_and_regime,
)
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from datetime import date

    from trades.config import AppConfig, TaxRegime


@dataclass(frozen=True)
class LiquidationEstimate:
    """What a full sale of every open lot, today, would leave you with, and the arithmetic behind that number.

    `pretax_value_usd` is the portfolio's current value; the gain buckets
    and `capital_gains_tax_usd` show how much of that value a hypothetical
    sale right now would owe in tax (see `taxes.liquidation_gain_buckets`);
    `after_tax_value_usd = pretax_value_usd - capital_gains_tax_usd` is what
    would actually be left over.
    """

    pretax_value_usd: float
    long_term_gain_usd: float
    short_term_gain_usd: float
    capital_gains_tax_usd: float
    after_tax_value_usd: float


@dataclass(frozen=True)
class TaxSummary:
    """The full tax view: realized gains/dividends by year, estimated tax owed, wash sales, and sale previews."""

    annual: pl.DataFrame
    tax_owed: pl.DataFrame
    wash_sales: pl.DataFrame
    sale_previews: pl.DataFrame
    after_tax_dollar_alpha_vs_hysa_usd: float
    liquidation_pretax_value_usd: float
    liquidation_long_term_gain_usd: float
    liquidation_short_term_gain_usd: float
    liquidation_capital_gains_tax_usd: float
    liquidation_value_usd: float


def _after_tax_dollar_alpha_vs_hysa(
    ledger: pl.DataFrame,
    config: AppConfig,
    as_of: date,
    regime: TaxRegime,
    status_change_date: date | None,
    marginal_ordinary_rate: float,
) -> float:
    """Dollar alpha vs. HYSA, using the after-tax rate in place of the raw published rate.

    Mirrors `overview_cards`'s pre-tax figure exactly, substituting an
    `after_tax_rate_lookup`-wrapped rate for the plain one — everything
    else about the comparison (replaying the same deposit/withdrawal
    history against a virtual savings account) is unchanged.

    Returns
    -------
    float
        Portfolio value minus the after-tax HYSA counterfactual value, as of `as_of`.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    flows = collect_if_lazy(external_cashflows(ledger))
    if flows.is_empty():
        return value

    after_tax_lookup = after_tax_rate_lookup(
        raw_hysa_rate_lookup(config), marginal_ordinary_rate, regime, status_change_date
    )
    hysa_value = hysa_counterfactual_value(flows, as_of, after_tax_lookup, config.returns.days_per_year)
    return value - hysa_value


def _liquidation_estimate(
    ledger: pl.DataFrame,
    config: AppConfig,
    as_of: date,
    regime: TaxRegime,
    marginal_ordinary_rate: float,
    qualified_ltcg_rate: float,
) -> LiquidationEstimate:
    """Estimate what selling every open lot right now, and paying the resulting tax, would leave you with.

    Every open lot is previewed as if sold `as_of` (see `taxes.preview_sale`)
    and taxed as `taxes.liquidation_tax_usd` describes — a resident alien's
    net gain in each holding-period bucket, a nonresident alien's nothing.
    This is a snapshot, not a projection: it says nothing about what
    selling gradually, or on a different future date, would owe.

    Returns
    -------
    LiquidationEstimate
        The pre-tax value, the gain buckets and tax a sale would trigger, and what would be left over.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    if result.open_lots.is_empty():
        return LiquidationEstimate(value, 0.0, 0.0, 0.0, value)

    previews = preview_sale(result.open_lots, ledger, price_lookup, as_of, config)
    long_term_gain, short_term_gain = liquidation_gain_buckets(previews)
    tax = liquidation_tax_usd(previews, regime, marginal_ordinary_rate, qualified_ltcg_rate)
    return LiquidationEstimate(value, long_term_gain, short_term_gain, tax, value - tax)


def tax_summary(ledger: pl.DataFrame, config: AppConfig, as_of: date) -> TaxSummary:
    """Assemble the full tax view: the annual report, estimated tax owed, flagged wash sales, and sale previews.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    as_of
        The date to preview open-lot sales, and value the after-tax comparisons, as of.

    Returns
    -------
    TaxSummary
        The full tax view, ready to serialize.
    """
    settings = load_settings(config)
    regime = resolved_tax_regime(config)
    status_change_date = settings.residency_status_change_date
    marginal_ordinary_rate = resolved_marginal_ordinary_rate(config)
    qualified_ltcg_rate = resolved_qualified_ltcg_rate(config)
    result = replay_ledger(ledger, config)

    annual = annual_tax_report(result.closed_lots, ledger, config, regime, status_change_date)
    owed = tax_owed_by_year_and_regime(
        annual, marginal_ordinary_rate, qualified_ltcg_rate, resolved_nra_dividend_tax_rate(config)
    )
    wash_sales = cast("pl.DataFrame", flag_wash_sales(result.closed_lots, ledger, config)).filter(
        pl.col("wash_sale_flag")
    )
    previews = preview_sale(result.open_lots, ledger, make_price_lookup(config), as_of, config)
    after_tax_alpha = _after_tax_dollar_alpha_vs_hysa(
        ledger, config, as_of, regime, status_change_date, marginal_ordinary_rate
    )
    liquidation = _liquidation_estimate(ledger, config, as_of, regime, marginal_ordinary_rate, qualified_ltcg_rate)

    return TaxSummary(
        annual=annual,
        tax_owed=owed,
        wash_sales=wash_sales,
        sale_previews=previews,
        after_tax_dollar_alpha_vs_hysa_usd=after_tax_alpha,
        liquidation_pretax_value_usd=liquidation.pretax_value_usd,
        liquidation_long_term_gain_usd=liquidation.long_term_gain_usd,
        liquidation_short_term_gain_usd=liquidation.short_term_gain_usd,
        liquidation_capital_gains_tax_usd=liquidation.capital_gains_tax_usd,
        liquidation_value_usd=liquidation.after_tax_value_usd,
    )
