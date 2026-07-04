import type {
  AllocationRow,
  BenchmarkSetting,
  BenchmarkSettingUpdate,
  DataQualityRow,
  DollarChart,
  GrowthOf100Point,
  HysaRates,
  HysaSettings,
  LedgerEvent,
  LotsTable,
  MonthlyPnl,
  MonthlyPnlBySymbol,
  Overview,
  RiskStat,
  SymbolPriceStatus,
  SymbolSearchResult,
  SyncProgress,
  SyncResult,
  TargetAllocation,
} from '@/types/portfolio'

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

// Date range params shared by every chart/stat endpoint — omitted keys let
// the backend default to "since inception" through today (never "just
// today" alone, per the anti-overmonitoring default).
export interface DateRange {
  start?: string
  end?: string
}

function withRange(path: string, range?: DateRange): string {
  const params = new URLSearchParams()
  if (range?.start) params.set('start', range.start)
  if (range?.end) params.set('end', range.end)
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export const api = {
  overview: (asOf?: string) =>
    request<Overview>(asOf ? `/api/overview?as_of=${asOf}` : '/api/overview'),
  dollarChart: (range?: DateRange) => request<DollarChart>(withRange('/api/chart/dollar', range)),
  growthOf100Chart: (range?: DateRange) =>
    request<GrowthOf100Point[]>(withRange('/api/chart/growth-of-100', range)),
  monthlyPnl: (range?: DateRange) => request<MonthlyPnl[]>(withRange('/api/chart/monthly-pnl', range)),
  monthlyPnlBySymbol: (range?: DateRange) =>
    request<MonthlyPnlBySymbol[]>(withRange('/api/chart/monthly-pnl/by-symbol', range)),
  allocation: (asOf?: string) =>
    request<AllocationRow[]>(asOf ? `/api/allocation?as_of=${asOf}` : '/api/allocation'),
  targetAllocation: () => request<TargetAllocation>('/api/settings/target-allocation'),
  setTargetAllocation: (target: TargetAllocation) =>
    request<TargetAllocation>('/api/settings/target-allocation', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target),
    }),
  lots: (asOf?: string) => request<LotsTable>(asOf ? `/api/lots?as_of=${asOf}` : '/api/lots'),
  risk: (range?: DateRange) => request<RiskStat>(withRange('/api/risk', range)),
  dataQuality: () => request<DataQualityRow[]>('/api/data-quality'),
  ledgerExport: () => request<LedgerEvent[]>('/api/ledger/export'),
  sync: () => request<SyncResult>('/api/sync', { method: 'POST' }),
  syncProgress: () => request<SyncProgress>('/api/sync/progress'),
  hysaRates: () => request<HysaRates>('/api/hysa-rates'),
  hysaSettings: () => request<HysaSettings>('/api/settings/hysa'),
  setHysaSettings: (settings: HysaSettings) =>
    request<HysaSettings>('/api/settings/hysa', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  benchmarkSetting: () => request<BenchmarkSetting>('/api/settings/benchmark'),
  setBenchmarkSetting: (setting: BenchmarkSettingUpdate) =>
    request<BenchmarkSetting>('/api/settings/benchmark', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setting),
    }),
  searchSymbols: (query: string) =>
    request<SymbolSearchResult[]>(`/api/symbols/search?q=${encodeURIComponent(query)}`),
  ensureSymbolPriced: (symbol: string) =>
    request<SymbolPriceStatus>(`/api/symbols/${encodeURIComponent(symbol)}/ensure-priced`, { method: 'POST' }),
}
