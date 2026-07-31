"""Every Pydantic request/response model used by `trades.api`'s routers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from db.money import Rate
from trades.config import TaxRegime, validate_iana_zone_name
from trades.dashboard.cash_sitting import WarningLevel


class SyncProgress(BaseModel):
    """A snapshot of an in-flight (or just-finished) sync, for the frontend's progress bar."""

    step: str
    percent: float
    done: bool
    error: str | None = None


class Overview(BaseModel):
    """The overview card row: value, gain split, XIRR, excess value over a HYSA, TWR."""

    as_of: date
    value_usd: float
    gain_usd: float
    gain_pct: float | None
    realized_gain_usd: float
    unrealized_gain_usd: float
    xirr_pct: float | None
    xirr_is_provisional: bool
    excess_value_vs_hysa_usd: float
    twr_pct: float | None
    twr_annualized_pct: float | None
    timing_gap_pct: float | None
    total_deposited_usd: float
    total_withdrawn_usd: float
    total_dividends_gross_usd: float
    total_withholding_usd: float
    total_fees_usd: float
    last_synced_at: str | None


class DollarChartPoint(BaseModel):
    """One day's worth of the dollar chart's series."""

    date: date
    contributions_usd: float
    portfolio_value_usd: float
    hysa_value_usd: float
    benchmark_value_usd: float
    hysa_rate_pct: float


class ReallocationMarker(BaseModel):
    """A date where a sell funded a same-day buy of a different symbol."""

    date: date
    sold_symbols: list[str]
    bought_symbols: list[str]


class DollarChart(BaseModel):
    """The three/four-line dollar chart plus reallocation markers."""

    series: list[DollarChartPoint]
    reallocation_markers: list[ReallocationMarker]


class GrowthOf100Point(BaseModel):
    """One day's worth of the growth-of-$100 chart: NAV plus every benchmark, indexed to 100."""

    date: date
    portfolio_index: float | None
    hysa_index: float | None
    benchmark_index: float | None
    cpi_index: float | None
    hysa_rate_pct: float | None


class CashHistoryPoint(BaseModel):
    """One day's uninvested cash balance, plus what it would be worth invested immediately."""

    date: date
    cash: float
    benchmark_live_usd: float
    benchmark_realized_usd: float
    hysa_live_usd: float
    hysa_realized_usd: float


class CashSitting(BaseModel):
    """How long the current uninvested cash balance has been sitting idle, and what it's missed out on."""

    cash_usd: float
    sitting_since: date
    days_sitting: int
    warning_level: WarningLevel
    hypothetical_value_portfolio_usd: float
    missed_earnings_portfolio_usd: float
    hypothetical_value_benchmark_usd: float
    missed_earnings_benchmark_usd: float


class MonthlyPnlRow(BaseModel):
    """One calendar month's value change split into contributions and market gain."""

    month: str
    contributions_usd: float
    market_gain_usd: float


class MonthlyPnlBySymbolRow(BaseModel):
    """One (month, symbol) pair's value change split into that symbol's trading and market gain."""

    month: str
    symbol: str
    contribution_usd: float
    market_gain_usd: float


class AllocationRow(BaseModel):
    """One symbol's (or cash's) current-value allocation, against a target."""

    symbol: str
    value_usd: float
    current_pct: float
    target_pct: float
    drift_pct: float


class HysaSettingsUpdate(BaseModel):
    """Request body for `PUT /api/v1/trades/settings/hysa`."""

    bank_id: str | None = None
    fixed_rate_pct: Rate | None = None


class HysaSettings(BaseModel):
    """The persisted HYSA bank selection / fixed-rate override."""

    bank_id: str | None
    fixed_rate_pct: Rate | None


class BenchmarkSettingUpdate(BaseModel):
    """Request body for `PUT /api/v1/trades/settings/benchmark`."""

    symbol_override: str | None = None


class BenchmarkSetting(BaseModel):
    """The persisted benchmark symbol override, plus the default it falls back to."""

    symbol_override: str | None
    default_symbol: str


class TimezoneSettingUpdate(BaseModel):
    """Request body for `PUT /api/v1/trades/settings/timezone`.

    `local_zone` is the browser's own IANA zone name
    (`Intl.DateTimeFormat().resolvedOptions().timeZone`), reported once per
    session rather than picked from a list — see
    `trades.dashboard.settings.DashboardSettings.local_zone`.
    """

    local_zone: str | None = None

    @field_validator("local_zone")
    @classmethod
    def _validate_zone_name(cls, value: str | None) -> str | None:
        """Reject a `local_zone` that isn't a real IANA timezone name.

        Returns
        -------
        str or None
        """
        return validate_iana_zone_name(value) if value is not None else None


class TimezoneSetting(BaseModel):
    """The persisted display-timezone override, plus what it resolves to."""

    local_zone: str | None
    resolved_local_zone: str


class TaxSettingsUpdate(BaseModel):
    """Request body for `PUT /api/v1/trades/settings/tax`."""

    tax_enabled: bool
    tax_regime: TaxRegime | None
    residency_status_change_date: date | None
    w8ben_claimed: bool
    w8ben_treaty_rate_pct: Rate | None
    marginal_ordinary_rate_pct: Rate | None
    qualified_ltcg_rate_pct: Rate | None


class TaxSettings(BaseModel):
    """The persisted tax-reporting settings, alongside what the tax report actually resolves them to."""

    tax_enabled: bool
    tax_regime: TaxRegime | None
    resolved_tax_regime: TaxRegime
    residency_status_change_date: date | None
    w8ben_claimed: bool
    w8ben_treaty_rate_pct: Rate | None
    marginal_ordinary_rate_pct: Rate | None
    resolved_marginal_ordinary_rate_pct: Rate
    qualified_ltcg_rate_pct: Rate | None
    resolved_qualified_ltcg_rate_pct: Rate


class IbkrCredentialsUpdate(BaseModel):
    """Request body for `PUT /api/v1/trades/settings/ibkr`.

    Either field left `None` leaves that one exactly as it was — a query
    id entered with no token doesn't clear an existing token, the same
    partial-merge convention `PUT /api/v1/trades/settings/benchmark`/`/tax` use.
    """

    token: str | None = None
    query_id: str | None = None


class IbkrSettings(BaseModel):
    """Whether IBKR credentials are available, without ever exposing their value."""

    configured: bool
    token_set: bool
    query_id_set: bool


class VerifyResult(BaseModel):
    """Whether IBKR accepted the configured credentials."""

    ok: bool
    error: str | None


class BrokerConnection(BaseModel):
    """One of this user's live broker connections — what an accounting account may link its value to.

    `connection_id` is the raw `trades.broker_connections.id`, not a
    natural key, because it is what
    `accounting.models.Account.broker_connection_id` foreign-keys to. This
    endpoint exists so the frontend can offer only connections that
    actually exist: the link is a real foreign key now (DB-audit move #1),
    so "IBKR credentials are configured" is no longer close enough — the
    connection row is only created by the first sync, and until then there
    is nothing to point at.
    """

    connection_id: uuid.UUID
    broker: str


class AnnualTaxRow(BaseModel):
    """One (year, regime) pair's realized gains and dividend income."""

    year: int
    regime: TaxRegime
    long_term_gain_usd: float
    short_term_gain_usd: float
    qualified_dividends_usd: float
    ordinary_dividends_usd: float
    ordinary_interest_usd: float
    withholding_tax_usd: float


class TaxOwedRow(AnnualTaxRow):
    """One (year, regime) pair's estimated tax bill, netted against withholding already paid."""

    capital_gains_tax_usd: float
    dividend_tax_usd: float
    total_tax_usd: float
    balance_due_usd: float


class WashSaleRow(BaseModel):
    """A closed lot whose loss might be disallowed by a nearby repurchase.

    `closed_by_event_id` is returned by `taxes.flag_wash_sales` (it's part
    of `lots.ClosedLot`) but is not currently declared on the hand-written
    `web/src/types/portfolio.ts` `WashSaleRow` interface — a stale-TS
    discrepancy kept here rather than silently dropped, since dropping it
    would be a real (if minor) behavior change versus what the endpoint
    returns today.
    """

    lot_id: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    shares: float
    cost_per_share: float
    exit_price: float
    realized_gain: float
    term: Literal["LONG", "SHORT"]
    closed_by_event_id: str
    dividends_received: float
    wash_sale_flag: bool


class SalePreviewRow(BaseModel):
    """What selling one open lot today, without actually selling it, would look like."""

    lot_id: str
    symbol: str
    shares: float
    days_held: int
    term: Literal["LONG", "SHORT"]
    unrealized_gain_usd: float
    would_wash_sale: bool


class TaxReport(BaseModel):
    """The full tax view: the annual report, estimated tax owed, flagged wash sales, and sale previews."""

    annual: list[AnnualTaxRow]
    tax_owed: list[TaxOwedRow]
    wash_sales: list[WashSaleRow]
    sale_previews: list[SalePreviewRow]
    after_tax_excess_value_vs_hysa_usd: float
    liquidation_pretax_value_usd: float
    liquidation_long_term_gain_usd: float
    liquidation_short_term_gain_usd: float
    liquidation_capital_gains_tax_usd: float
    liquidation_value_usd: float


class HysaBank(BaseModel):
    """One bank known to the HYSA rate cache."""

    bank_id: str
    bank_name: str


class HysaRatePoint(BaseModel):
    """One (bank, rate-change date, APY) triple."""

    bank_id: str
    bank_name: str
    rate_date: date
    apy_pct: float


class HysaRates(BaseModel):
    """Every bank's known rate history, for the bank picker and APY comparison chart."""

    banks: list[HysaBank]
    history: list[HysaRatePoint]
    default_bank_id: str


class SymbolSearchResult(BaseModel):
    """One ticker-symbol search match."""

    symbol: str
    name: str
    exchange: str


class SymbolPriceStatus(BaseModel):
    """Whether a symbol's price cache needed a refresh, and its last cached date."""

    symbol: str
    was_stale: bool
    last_price_date: str | None


class OpenLotRow(BaseModel):
    """One open lot, with its return since it was opened."""

    lot_id: str
    symbol: str
    opened_at: datetime
    shares: float
    cost_per_share: float
    dividends_received: float
    current_price: float
    days_held: int
    raw_return_pct: float
    annualized_return_pct: float | None


class ClosedLotRow(BaseModel):
    """One closed lot, with its total return and its excess over a HYSA, over its holding window.

    `closed_by_event_id` is part of `lots.ClosedLot` but is not currently
    declared on the hand-written `web/src/types/portfolio.ts` `ClosedLot`
    interface — a stale-TS discrepancy kept here rather than silently
    dropped, since dropping it would be a real (if minor) behavior change
    versus what the endpoint returns today.
    """

    lot_id: str
    symbol: str
    opened_at: datetime
    closed_at: datetime
    shares: float
    cost_per_share: float
    exit_price: float
    realized_gain: float
    term: Literal["LONG", "SHORT"]
    closed_by_event_id: str
    dividends_received: float
    days_held: int | None
    total_return_pct: float | None
    excess_return_vs_hysa_pct: float | None


class SymbolRollupRow(BaseModel):
    """One symbol's lifecycle stats and money-weighted return."""

    symbol: str
    invested: float
    proceeds_received: float
    dividends_received: float
    current_value: float
    realized_gain: float
    unrealized_gain: float
    status: Literal["open", "closed"]
    xirr: float


class LotsTable(BaseModel):
    """The trade-level table: open lots, closed lots, per-symbol rollup."""

    open_lots: list[OpenLotRow]
    closed_lots: list[ClosedLotRow]
    symbol_rollup: list[SymbolRollupRow]


class RiskStat(BaseModel):
    """The largest peak-to-trough NAV decline over a window."""

    max_drawdown_pct: float


class DataQualityRow(BaseModel):
    """The last cached price date for one symbol."""

    symbol: str
    last_price_date: date | None


class SyncStep(BaseModel):
    """One independent leg of a sync — a UI-friendly label, not the underlying provider's name."""

    label: str
    ok: bool
    error: str | None = None


class SyncResult(BaseModel):
    """The outcome of a sync: what changed in the ledger, plus each independent leg's own success/failure."""

    synced_at: str | None
    new_event_count: int
    total_event_count: int
    steps: list[SyncStep]
