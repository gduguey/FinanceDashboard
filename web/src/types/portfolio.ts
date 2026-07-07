// Mirrors the JSON shapes returned by src/trades/api.py — one type per
// endpoint response, kept in this file so a backend field rename is a
// one-place change on the frontend too.

export interface Overview {
  as_of: string
  value_usd: number
  gain_usd: number
  gain_pct: number | null
  realized_gain_usd: number
  unrealized_gain_usd: number
  xirr_pct: number | null
  xirr_is_provisional: boolean
  dollar_alpha_vs_hysa_usd: number
  twr_pct: number | null
  twr_annualized_pct: number | null
  timing_gap_pct: number | null
  total_deposited_usd: number
  total_withdrawn_usd: number
  total_dividends_gross_usd: number
  total_withholding_usd: number
  total_fees_usd: number
  last_synced_at: string | null
}

export interface DollarChartPoint {
  date: string
  contributions_usd: number
  portfolio_value_usd: number
  hysa_value_usd: number
  benchmark_value_usd: number
  hysa_rate_pct: number
}

export interface ReallocationMarker {
  date: string
  sold_symbols: string[]
  bought_symbols: string[]
}

export interface DollarChart {
  series: DollarChartPoint[]
  reallocation_markers: ReallocationMarker[]
}

export interface GrowthOf100Point {
  date: string
  portfolio_index: number | null
  hysa_index: number | null
  benchmark_index: number | null
  cpi_index: number | null
  hysa_rate_pct: number | null
}

export interface CashHistoryPoint {
  date: string
  cash: number
}

export type CashSittingWarningLevel = 'none' | 'light' | 'heavy'

export interface CashSitting {
  cash_usd: number
  sitting_since: string
  days_sitting: number
  warning_level: CashSittingWarningLevel
  hypothetical_value_portfolio_usd: number
  missed_earnings_portfolio_usd: number
  hypothetical_value_benchmark_usd: number
  missed_earnings_benchmark_usd: number
}

export interface MonthlyPnl {
  month: string
  contributions_usd: number
  market_gain_usd: number
}

export interface MonthlyPnlBySymbol {
  month: string
  symbol: string
  contribution_usd: number
  market_gain_usd: number
}

export interface AllocationRow {
  symbol: string
  value_usd: number
  current_pct: number
  target_pct: number
  drift_pct: number
}

export type TargetAllocation = Record<string, number>

export interface OpenLot {
  lot_id: string
  symbol: string
  opened_at: string
  shares: number
  cost_per_share: number
  dividends_received: number
  current_price: number
  days_held: number
  raw_return_pct: number
  annualized_return_pct: number | null
}

export interface ClosedLot {
  lot_id: string
  symbol: string
  opened_at: string
  closed_at: string
  shares: number
  cost_per_share: number
  exit_price: number
  realized_gain: number
  term: 'LONG' | 'SHORT'
  dividends_received: number
  days_held: number
  total_return_pct: number
  alpha_vs_hysa_pct: number
}

export interface SymbolRollup {
  symbol: string
  invested: number
  proceeds_received: number
  dividends_received: number
  current_value: number
  realized_gain: number
  unrealized_gain: number
  status: 'open' | 'closed'
  xirr: number
}

export interface LotsTable {
  open_lots: OpenLot[]
  closed_lots: ClosedLot[]
  symbol_rollup: SymbolRollup[]
}

export interface RiskStat {
  max_drawdown_pct: number
}

export interface DataQualityRow {
  symbol: string
  last_price_date: string | null
}

export interface LedgerEvent {
  event_id: string
  event_datetime: string
  symbol: string
  event_type: string
  shares: number | null
  price: number | null
  amount: number
  currency: string
  meta: Record<string, string>
}

export interface SyncResult {
  synced_at: string | null
  new_event_count: number
  total_event_count: number
  symbols_refreshed: string[]
}

export interface HysaBank {
  bank_id: string
  bank_name: string
}

export interface HysaRatePoint {
  bank_id: string
  bank_name: string
  rate_date: string
  apy_pct: number
}

export interface HysaRates {
  banks: HysaBank[]
  history: HysaRatePoint[]
  default_bank_id: string
}

export interface HysaSettings {
  bank_id: string | null
  fixed_rate_pct: number | null
}

export interface BenchmarkSetting {
  symbol_override: string | null
  default_symbol: string
}

export interface BenchmarkSettingUpdate {
  symbol_override: string | null
}

export interface IbkrSettings {
  configured: boolean
  token_set: boolean
  query_id_set: boolean
}

export interface IbkrSettingsUpdate {
  token?: string
  query_id?: string
}

export interface SymbolSearchResult {
  symbol: string
  name: string
  exchange: string
}

export interface SymbolPriceStatus {
  symbol: string
  was_stale: boolean
  last_price_date: string | null
}

export interface SyncProgress {
  step: string
  percent: number
  done: boolean
  error: string | null
}

export type TaxRegime = 'NRA' | 'RESIDENT'

export interface TaxSettings {
  tax_enabled: boolean
  tax_regime: TaxRegime | null
  resolved_tax_regime: TaxRegime
  residency_status_change_date: string | null
  w8ben_claimed: boolean
  w8ben_treaty_rate_pct: number | null
  marginal_ordinary_rate_pct: number | null
  resolved_marginal_ordinary_rate_pct: number
  qualified_ltcg_rate_pct: number | null
  resolved_qualified_ltcg_rate_pct: number
}

export interface TaxSettingsUpdate {
  tax_enabled: boolean
  tax_regime: TaxRegime | null
  residency_status_change_date: string | null
  w8ben_claimed: boolean
  w8ben_treaty_rate_pct: number | null
  marginal_ordinary_rate_pct: number | null
  qualified_ltcg_rate_pct: number | null
}

export interface AnnualTaxRow {
  year: number
  regime: TaxRegime
  long_term_gain_usd: number
  short_term_gain_usd: number
  qualified_dividends_usd: number
  ordinary_dividends_usd: number
  ordinary_interest_usd: number
  withholding_tax_usd: number
}

export interface WashSaleRow {
  lot_id: string
  symbol: string
  opened_at: string
  closed_at: string
  shares: number
  cost_per_share: number
  exit_price: number
  realized_gain: number
  term: 'LONG' | 'SHORT'
  dividends_received: number
  wash_sale_flag: boolean
}

export interface SalePreviewRow {
  lot_id: string
  symbol: string
  shares: number
  days_held: number
  term: 'LONG' | 'SHORT'
  unrealized_gain_usd: number
  would_wash_sale: boolean
}

export interface TaxOwedRow extends AnnualTaxRow {
  capital_gains_tax_usd: number
  dividend_tax_usd: number
  total_tax_usd: number
  balance_due_usd: number
}

export interface TaxReport {
  annual: AnnualTaxRow[]
  tax_owed: TaxOwedRow[]
  wash_sales: WashSaleRow[]
  sale_previews: SalePreviewRow[]
  after_tax_dollar_alpha_vs_hysa_usd: number
  liquidation_pretax_value_usd: number
  liquidation_long_term_gain_usd: number
  liquidation_short_term_gain_usd: number
  liquidation_capital_gains_tax_usd: number
  liquidation_value_usd: number
}
