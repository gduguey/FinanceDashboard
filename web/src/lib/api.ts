import { fetchAllPages, type Page } from '@/lib/paging'
import type {
  AllocationRow,
  BenchmarkSetting,
  BenchmarkSettingUpdate,
  BrokerConnection,
  CashHistoryPoint,
  CashSitting,
  ClosedLot,
  DataQualityRow,
  DollarChart,
  GrowthOf100Point,
  HysaRates,
  HysaSettings,
  HysaSettingsUpdate,
  IbkrSettings,
  IbkrSettingsUpdate,
  LedgerEvent,
  LedgerEventPage,
  LotsTable,
  MonthlyPnl,
  MonthlyPnlBySymbol,
  OpenLot,
  Overview,
  RiskStat,
  SymbolPriceStatus,
  SymbolRollup,
  SymbolSearchResult,
  SyncRun,
  SyncRunPage,
  TargetAllocation,
  TargetAllocationPatch,
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

// Starts a sync and returns the run that reports on it — the pull itself
// happens on the server, in the background, and the POST answers 202 without
// waiting for it.
//
// A 409 is not an error here. It means this account already has a sync going,
// which is the same situation the caller wanted to be in, and the response
// says which run that is in its own `Location`. Surfacing it as a failure
// would show "Sync failed" to someone whose sync is running perfectly well —
// so the run id is read off the header and the existing run is returned
// instead. Every other non-OK status is a real failure and still throws.
async function startSync(): Promise<SyncRun> {
  const response = await fetch(`${TRADES_API_BASE}/sync-runs`, { method: 'POST' })
  if (response.status === 409) {
    const location = response.headers.get('Location')
    if (location) return await api.syncRun(location.split('/').pop() as string)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.detail ?? `${response.status} ${response.statusText}`)
  }
  return (await parseBody(response)) as SyncRun
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
  // RFC 7386 merge patch over the `symbol -> pct` map: a number sets that
  // symbol's target, an explicit `null` removes it, and an absent symbol is
  // left as-is. The media type is what tells the server to read the body that
  // way rather than as a whole-map replacement.
  patchTargetAllocation: (patch: TargetAllocationPatch) =>
    request<TargetAllocation>('/settings/target-allocation', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/merge-patch+json' },
      body: JSON.stringify(patch),
    }),
  // Three paged collections, each walked to the end and reassembled into the
  // one object every consumer wants (C4a). The payload per response is
  // bounded and nothing is silently truncated: `LotsTable.tsx` counts both
  // lot lists for its tab labels and sorts and day-aggregates them in the
  // browser, and the Settings export writes all three to files a user
  // believes are complete — so a page would be the wrong answer here, and a
  // page walk is the same pattern `ledgerExport` uses.
  //
  // Walked in parallel because the three requests are independent; each one
  // costs its own FIFO replay of the ledger, which is affordable since C4b
  // made that replay linear and was the reason this bound was refused in
  // PR D.
  lots: async (asOf?: string): Promise<LotsTable> => {
    const asOfQuery = asOf ? `&as_of=${asOf}` : ''
    const page = <T>(collection: string) =>
      fetchAllPages<T>(({ limit, offset }) =>
        request<Page<T>>(`/lots/${collection}?limit=${limit}&offset=${offset}${asOfQuery}`),
      )
    const [open_lots, closed_lots, symbol_rollup] = await Promise.all([
      page<OpenLot>('open'),
      page<ClosedLot>('closed'),
      page<SymbolRollup>('symbols'),
    ])
    return { open_lots, closed_lots, symbol_rollup }
  },
  risk: (range?: DateRange) => request<RiskStat>(withRange('/risk', range)),
  cashHistory: (range?: DateRange) => request<CashHistoryPoint[]>(withRange('/chart/cash-history', range)),
  cashSitting: () => request<CashSitting>('/cash-sitting'),
  dataQuality: () => request<DataQualityRow[]>('/data-quality'),
  // Pages through the whole ledger rather than asking for one page: this is
  // a backup, so a response that silently stopped at the server's cap would
  // write a partial file the user believes is complete. Shares
  // `fetchAllPages` with the accounting client because both ledgers answer
  // with the same envelope — this one's `window_unit` is `"event"` (C3).
  ledgerExport: () =>
    fetchAllPages<LedgerEvent>(({ limit, offset }) =>
      request<LedgerEventPage>(`/ledger/export?limit=${limit}&offset=${offset}`),
    ),
  startSync,
  syncRun: (runId: string) => request<SyncRun>(`/sync-runs/${runId}`),
  // `limit=1` on the newest-first list, which is how a page that reloaded
  // mid-sync finds the run whose id it lost.
  latestSyncRun: () => request<SyncRunPage>('/sync-runs?limit=1'),
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
