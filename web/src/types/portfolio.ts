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
  total_dividends_usd: number
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
