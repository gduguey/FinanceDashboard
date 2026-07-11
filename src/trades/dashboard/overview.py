"""Overview card row — headline portfolio stats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import polars as pl

from trades.dashboard.settings import hysa_rate_lookup
from trades.dashboard.valuation import daily_portfolio_values, make_price_lookup
from trades.ledger.counterfactuals import hysa_counterfactual_value
from trades.ledger.metrics import realized_gain_total, unrealized_gain, xirr
from trades.ledger.nav import nav_series, time_weighted_return
from trades.ledger.replay import external_cashflows, portfolio_value, replay_ledger
from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig
    from trades.dashboard.settings import DashboardSettings
    from trades.ledger.nav import PeriodReturn


@dataclass(frozen=True)
class OverviewCards:
    """Headline portfolio stats for the overview card row."""

    as_of: date
    value_usd: float
    gain_usd: float
    gain_pct: float | None
    realized_gain_usd: float
    unrealized_gain_usd: float
    xirr_pct: float | None
    xirr_is_provisional: bool
    dollar_alpha_vs_hysa_usd: float
    twr_pct: float | None
    twr_annualized_pct: float | None
    timing_gap_pct: float | None
    total_deposited_usd: float
    total_withdrawn_usd: float
    total_dividends_gross_usd: float
    total_withholding_usd: float
    total_fees_usd: float


def _xirr_and_twr(
    ledger: pl.DataFrame,
    flows: pl.DataFrame,
    price_lookup: Callable[[str, date], float | None],
    value: float,
    as_of: date,
    config: AppConfig,
) -> tuple[float, bool, PeriodReturn]:
    """Compute portfolio XIRR and TWR since the first external flow.

    Extracted out of `overview_cards` purely to keep that function's
    local-variable count down; the two are computed together because both
    need the same `first_flow_date`.

    Returns
    -------
    tuple[float, bool, PeriodReturn]
        XIRR as a percentage, whether it's provisional (< 12 months of
        history), and the TWR since the first external flow.
    """
    first_flow_date = cast("date", min(flows["event_datetime"].dt.date().to_list()))
    dates = [*flows["event_datetime"].dt.date().to_list(), as_of]
    amounts = [*flows["amount"].to_list(), value]
    xirr_pct = xirr(dates, amounts, config) * 100
    is_provisional = (as_of - first_flow_date).days < config.returns.annualization_days

    daily_values = daily_portfolio_values(ledger, price_lookup, first_flow_date, as_of, config)
    nav = nav_series(daily_values, flows)
    twr = time_weighted_return(nav, start=first_flow_date, end=as_of, config=config)
    return xirr_pct, is_provisional, twr


def _gross_deposits_and_dividends(ledger: pl.DataFrame) -> tuple[float, float, float, float, float]:
    """Sum gross `DEPOSIT`, `WITHDRAWAL`, `DIVIDEND`, `WITHHOLDING`, and `FEE` amounts.

    Deliberately gross, not netted against each other: shown side by side
    so `value = (deposited - withdrawn) + gain` visibly reconciles, rather
    than a lone "money in" figure that looks wrong once a withdrawal has
    happened (deposits alone won't explain the gap to `value`).

    Returns
    -------
    tuple[float, float, float, float, float]
        `(total_deposited, total_withdrawn, total_dividends_gross, total_withholding, total_fees)`.
    """
    total_deposited = float(ledger.filter(pl.col("event_type") == "DEPOSIT")["amount"].sum())
    total_withdrawn = float(ledger.filter(pl.col("event_type") == "WITHDRAWAL")["amount"].sum())
    total_dividends = float(ledger.filter(pl.col("event_type") == "DIVIDEND")["amount"].sum())
    total_withholding = float(ledger.filter(pl.col("event_type") == "WITHHOLDING")["amount"].sum())
    total_fees = float(ledger.filter(pl.col("event_type") == "FEE")["amount"].sum())
    return total_deposited, total_withdrawn, total_dividends, total_withholding, total_fees


def overview_cards(ledger: pl.DataFrame, config: AppConfig, settings: DashboardSettings, as_of: date) -> OverviewCards:
    """Assemble the overview card row: value, gain split, XIRR, dollar alpha, TWR.

    Parameters
    ----------
    ledger
        The full ledger, in chronological order.
    config
        Application configuration.
    settings
        This user's persisted dashboard settings.
    as_of
        The date to value the portfolio as of.

    Returns
    -------
    OverviewCards
        The headline stats, ready to serialize.
    """
    price_lookup = make_price_lookup(config)
    result = replay_ledger(ledger, config)
    value = portfolio_value(result, price_lookup, as_of)
    flows = collect_if_lazy(external_cashflows(ledger))

    realized = realized_gain_total(result.closed_lots)
    unrealized = unrealized_gain(result.open_lots, price_lookup, as_of)
    gain = realized + unrealized
    net_invested = -float(flows["amount"].sum()) if not flows.is_empty() else 0.0
    gain_pct = (gain / net_invested * 100) if net_invested else None

    xirr_pct: float | None = None
    is_provisional = False
    twr: PeriodReturn | None = None
    if not flows.is_empty():
        xirr_pct, is_provisional, twr = _xirr_and_twr(ledger, flows, price_lookup, value, as_of, config)

    hysa_value = (
        hysa_counterfactual_value(flows, as_of, hysa_rate_lookup(config, settings), config.returns.days_per_year)
        if not flows.is_empty()
        else 0.0
    )
    timing_gap_pct = (
        xirr_pct - twr.annualized_pct if xirr_pct is not None and twr and twr.annualized_pct is not None else None
    )
    gross = _gross_deposits_and_dividends(ledger)

    return OverviewCards(
        as_of=as_of,
        value_usd=value,
        gain_usd=gain,
        gain_pct=gain_pct,
        realized_gain_usd=realized,
        unrealized_gain_usd=unrealized,
        xirr_pct=xirr_pct,
        xirr_is_provisional=is_provisional,
        dollar_alpha_vs_hysa_usd=value - hysa_value,
        twr_pct=twr.raw_pct if twr else None,
        twr_annualized_pct=twr.annualized_pct if twr else None,
        timing_gap_pct=timing_gap_pct,
        total_deposited_usd=gross[0],
        total_withdrawn_usd=gross[1],
        total_dividends_gross_usd=gross[2],
        total_withholding_usd=gross[3],
        total_fees_usd=gross[4],
    )
