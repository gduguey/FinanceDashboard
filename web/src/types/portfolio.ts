// Mirrors the JSON shapes returned by src/trades/api.py — one type per
// endpoint response, kept in this file so a backend field rename is a
// one-place change on the frontend too.

export interface Summary {
  as_of_date: string
  total_invested_usd: number
  current_value_usd: number
  total_gain_usd: number
  total_gain_pct: number | null
  portfolio_alpha_pct: number
  hysa_annual_rate: number
  symbol_count: number
  last_synced_at: string | null
}

export interface Trade {
  trade_date: string
  symbol: string
  shares: number
  usd_spent: number
  usd_per_share: number
  n_trades: number
}

// One row per month; every symbol seen gets its own numeric column plus a
// "Total" column — dynamic per portfolio, so only `month`/`Total` are named.
export type MonthlyInvested = { month: string; Total: number } & Record<string, number | string>

export interface DailyInvestment {
  trade_date: string
  usd_spent: number
  cumulative_usd_spent: number
  days_since_previous_investment: number | null
}

export interface PieSlice {
  name: string
  value: number
}

export type PieOptions = Record<string, PieSlice[]>

export interface ReturnRow {
  trade_date: string
  symbol: string
  price_paid: number
  current_price: number
  days_held: number
  total_return_pct: number
  annualized_return_pct: number
  hysa_period_return_pct: number
  alpha_period_pct: number
  usd_spent: number
}

export interface ReturnCurvePoint {
  symbol: string
  days_held: number
  annualized_return_pct: number
}

export interface ReturnCurveTrendPoint {
  days_held: number
  annualized_return_pct: number
}

export interface ReturnCurve {
  points: ReturnCurvePoint[]
  trend: ReturnCurveTrendPoint[]
  hysa_annual_rate_pct: number
}

export interface SyncResult {
  synced_at: string
  new_event_count: number
  total_event_count: number
  symbols_refreshed: string[]
}
