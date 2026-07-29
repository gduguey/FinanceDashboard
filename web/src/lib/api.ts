import type {
  AllocationRow,
  BenchmarkSetting,
  BenchmarkSettingUpdate,
  BrokerConnection,
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
  TaxReport,
  TaxSettings,
  TaxSettingsUpdate,
  TimezoneSetting,
  TimezoneSettingUpdate,
  VerifyResult,
} from '@/types/portfolio'

export class ApiError extends Error {}

// One row changed elsewhere (another tab, another device, or just an
// earlier request from this same tab) since the client last read it —
// the 409 the backend's per-row optimistic concurrency raises
// (`db.base.check_and_bump_row_version`, behind `expected_version` on
// goals / transfer rules / category patterns). Named for the row, not
// the store: the whole-store and whole-settings counters that used to
// share this error are gone, and only per-row conflicts can reach the UI
// now. Defined here rather than in `accountingApi.ts` — the only module
// that can actually throw it — so it sits with `ApiError` in one error
// taxonomy, which is where App.tsx's mutationCache imports it from.
export class RowVersionConflictError extends ApiError {}

// Where this module's endpoints live, declared once rather than repeated in
// every path below — the mirror image of `trades.api.api`'s own router prefix,
// so the two move together. `/api` keeps a versioned path from being swallowed
// by the SPA catch-all that serves `index.html`; `trades` is the module
// namespace (`accountingApi.ts` has its own). Exported for the handful of
// callers that need a URL rather than a parsed response — a browser-driven CSV
// download, which cannot go through `request`.
export const TRADES_API_BASE = '/api/v1/trades'

// A 204 has no body at all, so `response.json()` on one throws
// "Unexpected end of JSON input" — which is why this check is not an
// optimization. Every delete answers 204 (see `accounting.api.routers.*`),
// so without this branch every delete in the app fails after succeeding on
// the server. Shared by `accountingApi.ts`'s own wrapper, which has the same
// hazard.
export async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  return await response.json()
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${TRADES_API_BASE}${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.detail ?? `${response.status} ${response.statusText}`)
  }
  return (await parseBody(response)) as T
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
  overview: (asOf?: string) => request<Overview>(asOf ? `/overview?as_of=${asOf}` : '/overview'),
  dollarChart: (range?: DateRange) => request<DollarChart>(withRange('/chart/dollar', range)),
  growthOf100Chart: (range?: DateRange) => request<GrowthOf100Point[]>(withRange('/chart/growth-of-100', range)),
  monthlyPnl: (range?: DateRange) => request<MonthlyPnl[]>(withRange('/chart/monthly-pnl', range)),
  monthlyPnlBySymbol: (range?: DateRange) =>
    request<MonthlyPnlBySymbol[]>(withRange('/chart/monthly-pnl/by-symbol', range)),
  allocation: (asOf?: string) => request<AllocationRow[]>(asOf ? `/allocation?as_of=${asOf}` : '/allocation'),
  targetAllocation: () => request<TargetAllocation>('/settings/target-allocation'),
  setTargetAllocation: (target: TargetAllocation) =>
    request<TargetAllocation>('/settings/target-allocation', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target),
    }),
  lots: (asOf?: string) => request<LotsTable>(asOf ? `/lots?as_of=${asOf}` : '/lots'),
  risk: (range?: DateRange) => request<RiskStat>(withRange('/risk', range)),
  cashHistory: (range?: DateRange) => request<CashHistoryPoint[]>(withRange('/chart/cash-history', range)),
  cashSitting: () => request<CashSitting>('/cash-sitting'),
  dataQuality: () => request<DataQualityRow[]>('/data-quality'),
  ledgerExport: () => request<LedgerEvent[]>('/ledger/export'),
  sync: () => request<SyncResult>('/sync', { method: 'POST' }),
  syncProgress: () => request<SyncProgress>('/sync/progress'),
  hysaRates: () => request<HysaRates>('/hysa-rates'),
  hysaSettings: () => request<HysaSettings>('/settings/hysa'),
  setHysaSettings: (settings: HysaSettingsUpdate) =>
    request<HysaSettings>('/settings/hysa', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  benchmarkSetting: () => request<BenchmarkSetting>('/settings/benchmark'),
  setBenchmarkSetting: (setting: BenchmarkSettingUpdate) =>
    request<BenchmarkSetting>('/settings/benchmark', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setting),
    }),
  searchSymbols: (query: string) => request<SymbolSearchResult[]>(`/symbols/search?q=${encodeURIComponent(query)}`),
  ensureSymbolPriced: (symbol: string) =>
    request<SymbolPriceStatus>(`/symbols/${encodeURIComponent(symbol)}/ensure-priced`, { method: 'POST' }),
  taxSettings: () => request<TaxSettings>('/settings/tax'),
  setTaxSettings: (settings: TaxSettingsUpdate) =>
    request<TaxSettings>('/settings/tax', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  taxReport: (asOf?: string) => request<TaxReport>(asOf ? `/tax/report?as_of=${asOf}` : '/tax/report'),
  brokerConnections: () => request<BrokerConnection[]>('/broker-connections'),
  ibkrSettings: () => request<IbkrSettings>('/settings/ibkr'),
  setIbkrSettings: (update: IbkrSettingsUpdate) =>
    request<IbkrSettings>('/settings/ibkr', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    }),
  clearIbkrSettings: () => request<IbkrSettings>('/settings/ibkr', { method: 'DELETE' }),
  verifyIbkrSettings: () => request<VerifyResult>('/settings/ibkr/verify', { method: 'POST' }),
  timezoneSetting: () => request<TimezoneSetting>('/settings/timezone'),
  setTimezoneSetting: (setting: TimezoneSettingUpdate) =>
    request<TimezoneSetting>('/settings/timezone', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setting),
    }),
}
