import type {
  AllocationRow,
  BenchmarkSetting,
  BenchmarkSettingUpdate,
  CashHistoryPoint,
  CashSitting,
  DataQualityRow,
  DollarChart,
  GrowthOf100Point,
  HysaRates,
  HysaSettings,
  HysaSettingsUpdate,
  IbkrSettings,
  IbkrSettingsUpdate,
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
  TargetAllocationSetting,
  TaxReport,
  TaxSettings,
  TaxSettingsUpdate,
  TimezoneSetting,
  TimezoneSettingUpdate,
  VerifyResult,
} from '@/types/portfolio'

export class ApiError extends Error {}

// A change made elsewhere (another tab, another device, or just an
// earlier request from this same tab) since the last response this
// module saw with a `version` field — see App.tsx's mutationCache for how
// this is surfaced. Shared with `accountingApi.ts`, whose `request()`
// mirrors this one against `accounting`'s own `version` field, so
// App.tsx's single `error instanceof StoreVersionConflictError` check
// catches a conflict from either module.
export class StoreVersionConflictError extends ApiError {}

// The most recent `version` this module has seen out of any trades
// settings response — updated below on every response that carries one
// (`HysaSettings`, `BenchmarkSetting`, `TimezoneSetting`, `TaxSettings`,
// and `TargetAllocationSetting` — its endpoint now wraps the allocation
// map in an envelope carrying `version`, which `request()` reads before
// the client unwraps it) — and sent back on every non-GET request so the
// backend can tell whether anything changed in between.
let lastKnownDashboardSettingsVersion: number | null = null

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const headers = new Headers(init?.headers)
  if (method !== 'GET' && lastKnownDashboardSettingsVersion !== null) {
    headers.set('X-Expected-Dashboard-Settings-Version', String(lastKnownDashboardSettingsVersion))
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const message = body?.detail ?? `${response.status} ${response.statusText}`
    if (response.status === 409) throw new StoreVersionConflictError(message)
    throw new ApiError(message)
  }
  const data = (await response.json()) as T
  if (data && typeof data === 'object' && 'version' in data && typeof data.version === 'number') {
    lastKnownDashboardSettingsVersion = data.version
  }
  return data
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
  overview: (asOf?: string) => request<Overview>(asOf ? `/api/overview?as_of=${asOf}` : '/api/overview'),
  dollarChart: (range?: DateRange) => request<DollarChart>(withRange('/api/chart/dollar', range)),
  growthOf100Chart: (range?: DateRange) => request<GrowthOf100Point[]>(withRange('/api/chart/growth-of-100', range)),
  monthlyPnl: (range?: DateRange) => request<MonthlyPnl[]>(withRange('/api/chart/monthly-pnl', range)),
  monthlyPnlBySymbol: (range?: DateRange) =>
    request<MonthlyPnlBySymbol[]>(withRange('/api/chart/monthly-pnl/by-symbol', range)),
  allocation: (asOf?: string) => request<AllocationRow[]>(asOf ? `/api/allocation?as_of=${asOf}` : '/api/allocation'),
  // Unwrap the `{ target_allocation_pct, version }` envelope back to the bare map for callers. The
  // envelope's `version` is read (and cached) by `request()` itself before we unwrap, which is the
  // whole point — the old bare-dict shape had nowhere to carry it, so a save here left the cached
  // settings version stale and spuriously 409-ed the next hysa/benchmark/tax save.
  targetAllocation: () =>
    request<TargetAllocationSetting>('/api/settings/target-allocation').then((r) => r.target_allocation_pct),
  setTargetAllocation: (target: TargetAllocation) =>
    request<TargetAllocationSetting>('/api/settings/target-allocation', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target),
    }).then((r) => r.target_allocation_pct),
  lots: (asOf?: string) => request<LotsTable>(asOf ? `/api/lots?as_of=${asOf}` : '/api/lots'),
  risk: (range?: DateRange) => request<RiskStat>(withRange('/api/risk', range)),
  cashHistory: (range?: DateRange) => request<CashHistoryPoint[]>(withRange('/api/chart/cash-history', range)),
  cashSitting: () => request<CashSitting>('/api/cash-sitting'),
  dataQuality: () => request<DataQualityRow[]>('/api/data-quality'),
  ledgerExport: () => request<LedgerEvent[]>('/api/ledger/export'),
  sync: () => request<SyncResult>('/api/sync', { method: 'POST' }),
  syncProgress: () => request<SyncProgress>('/api/sync/progress'),
  hysaRates: () => request<HysaRates>('/api/hysa-rates'),
  hysaSettings: () => request<HysaSettings>('/api/settings/hysa'),
  setHysaSettings: (settings: HysaSettingsUpdate) =>
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
  searchSymbols: (query: string) => request<SymbolSearchResult[]>(`/api/symbols/search?q=${encodeURIComponent(query)}`),
  ensureSymbolPriced: (symbol: string) =>
    request<SymbolPriceStatus>(`/api/symbols/${encodeURIComponent(symbol)}/ensure-priced`, { method: 'POST' }),
  taxSettings: () => request<TaxSettings>('/api/settings/tax'),
  setTaxSettings: (settings: TaxSettingsUpdate) =>
    request<TaxSettings>('/api/settings/tax', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  taxReport: (asOf?: string) => request<TaxReport>(asOf ? `/api/tax/report?as_of=${asOf}` : '/api/tax/report'),
  ibkrSettings: () => request<IbkrSettings>('/api/settings/ibkr'),
  setIbkrSettings: (update: IbkrSettingsUpdate) =>
    request<IbkrSettings>('/api/settings/ibkr', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    }),
  clearIbkrSettings: () => request<IbkrSettings>('/api/settings/ibkr', { method: 'DELETE' }),
  verifyIbkrSettings: () => request<VerifyResult>('/api/settings/ibkr/verify', { method: 'POST' }),
  timezoneSetting: () => request<TimezoneSetting>('/api/settings/timezone'),
  setTimezoneSetting: (setting: TimezoneSettingUpdate) =>
    request<TimezoneSetting>('/api/settings/timezone', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setting),
    }),
}
