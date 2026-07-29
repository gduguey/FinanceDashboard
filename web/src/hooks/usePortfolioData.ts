import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api, type DateRange } from '@/lib/api'
import type {
  BenchmarkSettingUpdate,
  HysaSettingsUpdate,
  IbkrSettingsUpdate,
  TargetAllocation,
  TaxSettingsUpdate,
  TimezoneSettingUpdate,
} from '@/types/portfolio'

// One query key per endpoint, grouped under a shared "portfolio" root so a
// single invalidate (see useSync below) refreshes every panel at once.
const keys = {
  overview: ['portfolio', 'overview'],
  dollarChart: (range?: DateRange) => ['portfolio', 'chart', 'dollar', range ?? {}],
  growthOf100: (range?: DateRange) => ['portfolio', 'chart', 'growth-of-100', range ?? {}],
  monthlyPnl: (range?: DateRange) => ['portfolio', 'chart', 'monthly-pnl', range ?? {}],
  monthlyPnlBySymbol: (range?: DateRange) => ['portfolio', 'chart', 'monthly-pnl-by-symbol', range ?? {}],
  allocation: ['portfolio', 'allocation'],
  targetAllocation: ['portfolio', 'settings', 'target-allocation'],
  lots: ['portfolio', 'lots'],
  risk: (range?: DateRange) => ['portfolio', 'risk', range ?? {}],
  dataQuality: ['portfolio', 'data-quality'],
  cashHistory: (range?: DateRange) => ['portfolio', 'chart', 'cash-history', range ?? {}],
  cashSitting: ['portfolio', 'cash-sitting'],
  hysaRates: ['portfolio', 'hysa-rates'],
  hysaSettings: ['portfolio', 'settings', 'hysa'],
  benchmarkSetting: ['portfolio', 'settings', 'benchmark'],
  taxSettings: ['portfolio', 'settings', 'tax'],
  taxReport: ['portfolio', 'tax-report'],
  brokerConnections: ['portfolio', 'broker-connections'],
  ibkrSettings: ['portfolio', 'settings', 'ibkr'],
  ibkrVerify: ['portfolio', 'settings', 'ibkr', 'verify'],
  timezoneSetting: ['portfolio', 'settings', 'timezone'],
} as const

export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api.overview() })

export const useDollarChart = (range?: DateRange) =>
  useQuery({ queryKey: keys.dollarChart(range), queryFn: () => api.dollarChart(range) })

export const useGrowthOf100Chart = (range?: DateRange) =>
  useQuery({ queryKey: keys.growthOf100(range), queryFn: () => api.growthOf100Chart(range) })

export const useMonthlyPnl = (range?: DateRange) =>
  useQuery({ queryKey: keys.monthlyPnl(range), queryFn: () => api.monthlyPnl(range) })

export const useMonthlyPnlBySymbol = (range?: DateRange) =>
  useQuery({ queryKey: keys.monthlyPnlBySymbol(range), queryFn: () => api.monthlyPnlBySymbol(range) })

export const useAllocation = () => useQuery({ queryKey: keys.allocation, queryFn: () => api.allocation() })

export const useTargetAllocation = () => useQuery({ queryKey: keys.targetAllocation, queryFn: api.targetAllocation })

export function useSetTargetAllocation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (target: TargetAllocation) => api.setTargetAllocation(target),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.targetAllocation }),
  })
}

export const useLots = () => useQuery({ queryKey: keys.lots, queryFn: () => api.lots() })

export const useRisk = (range?: DateRange) => useQuery({ queryKey: keys.risk(range), queryFn: () => api.risk(range) })

export const useDataQuality = () => useQuery({ queryKey: keys.dataQuality, queryFn: api.dataQuality })

export const useCashHistory = (range?: DateRange) =>
  useQuery({ queryKey: keys.cashHistory(range), queryFn: () => api.cashHistory(range) })

export const useCashSitting = () => useQuery({ queryKey: keys.cashSitting, queryFn: () => api.cashSitting() })

export const useHysaRates = () => useQuery({ queryKey: keys.hysaRates, queryFn: api.hysaRates })

export const useHysaSettings = () => useQuery({ queryKey: keys.hysaSettings, queryFn: api.hysaSettings })

// HYSA/benchmark settings feed the dollar chart, growth-of-100 chart, and
// the overview's dollar-alpha card — changing one invalidates the whole
// portfolio tree, the same as a sync, rather than just its own settings key.
export function useSetHysaSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (settings: HysaSettingsUpdate) => api.setHysaSettings(settings),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

export const useBenchmarkSetting = () => useQuery({ queryKey: keys.benchmarkSetting, queryFn: api.benchmarkSetting })

export function useSetBenchmarkSetting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (setting: BenchmarkSettingUpdate) => api.setBenchmarkSetting(setting),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

// Fetches just one symbol's price history on the spot — used right after
// picking a new benchmark, so it takes effect without a full sync. Same
// whole-tree invalidation as a settings change, since the charts that
// read the refreshed prices are the same ones a benchmark change affects.
export function useEnsureSymbolPriced() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (symbol: string) => api.ensureSymbolPriced(symbol),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

// The user's live broker connections. An accounting account's value can
// only be pulled from one of these, because `accounts.broker_connection_id`
// is a real foreign key into `trades.broker_connections` — a connection row
// only exists once a sync has actually run, so "IBKR credentials are saved"
// is not the same question and no longer the one the account form asks.
export const useBrokerConnections = () => useQuery({ queryKey: keys.brokerConnections, queryFn: api.brokerConnections })

export const useIbkrSettings = () => useQuery({ queryKey: keys.ibkrSettings, queryFn: api.ibkrSettings })

export function useSetIbkrSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (update: IbkrSettingsUpdate) => api.setIbkrSettings(update),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.ibkrSettings }),
  })
}

export function useClearIbkrSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.clearIbkrSettings(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.ibkrSettings }),
  })
}

export type ConnectionState = 'none' | 'checking' | 'invalid' | 'connected'

// The one shared definition of "is IBKR actually connected" — Settings,
// the sidebar's Investments switch, and the onboarding page all read this
// same cached query rather than each deciding for themselves, so they can
// never disagree. A real auth check (one fast HTTP call to IBKR), not
// just "is a value present" — `configured` alone doesn't catch a
// wrong/expired token, only "something was typed in". Saving or clearing
// credentials invalidates `keys.ibkrSettings`, which — via React Query's
// prefix matching — invalidates this query too, so it always re-checks
// against whatever credential is actually in effect right now.
export function useIbkrConnectionStatus(): { state: ConnectionState; error: string | null } {
  const { data: settings } = useIbkrSettings()
  const configured = settings?.configured ?? false
  const verify = useQuery({
    queryKey: keys.ibkrVerify,
    queryFn: () => api.verifyIbkrSettings(),
    enabled: configured,
    staleTime: 30_000,
  })

  if (!configured) return { state: 'none', error: null }
  if (verify.isPending || !verify.data) return { state: 'checking', error: null }
  if (!verify.data.ok) return { state: 'invalid', error: verify.data.error }
  return { state: 'connected', error: null }
}

export function useSync() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

// Polls the in-flight sync's step/percent while `enabled` — the sync POST
// itself blocks until the whole thing finishes, so this is the only way
// to show live progress rather than a bare spinner for however long the
// slowest step (usually IBKR) takes.
export function useSyncProgress(enabled: boolean) {
  return useQuery({
    queryKey: ['sync-progress'],
    queryFn: api.syncProgress,
    enabled,
    refetchInterval: enabled ? 400 : false,
  })
}

export const useTaxSettings = () => useQuery({ queryKey: keys.taxSettings, queryFn: api.taxSettings })

export function useSetTaxSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (settings: TaxSettingsUpdate) => api.setTaxSettings(settings),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

export const useTaxReport = () => useQuery({ queryKey: keys.taxReport, queryFn: () => api.taxReport() })

export const useTimezoneSetting = () => useQuery({ queryKey: keys.timezoneSetting, queryFn: api.timezoneSetting })

// `last_synced_at` (in `overview`) is the only other cached value derived
// from this setting — narrower than the whole-tree invalidation
// HYSA/benchmark/tax use, since nothing else on the dashboard reads it.
export function useSetTimezoneSetting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (setting: TimezoneSettingUpdate) => api.setTimezoneSetting(setting),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.timezoneSetting })
      queryClient.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

// Reports the browser's own IANA zone once per mount — never user-picked
// from a list, the same "detected, not asked" approach as everything else
// under `Intl.DateTimeFormat().resolvedOptions().timeZone`. The `attempted`
// ref (not a `data`/mutation-state dependency) is what makes this exactly
// one attempt per session: `data` changes again once the mutation's own
// success invalidates `keys.timezoneSetting` and it refetches, which would
// otherwise re-run this effect against briefly-stale data and double-fire.
export function useSyncBrowserTimezone(): void {
  const { data } = useTimezoneSetting()
  const { mutate } = useSetTimezoneSetting()
  const attempted = useRef(false)

  useEffect(() => {
    if (!data || attempted.current) return
    attempted.current = true
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (data.local_zone !== detected) mutate({ local_zone: detected })
  }, [data, mutate])
}
