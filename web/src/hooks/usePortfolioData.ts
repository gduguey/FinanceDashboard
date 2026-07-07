import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type DateRange } from '@/lib/api'
import type {
  BenchmarkSettingUpdate,
  HysaSettings,
  IbkrSettingsUpdate,
  TargetAllocation,
  TaxSettingsUpdate,
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
  ibkrSettings: ['portfolio', 'settings', 'ibkr'],
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

export const useTargetAllocation = () =>
  useQuery({ queryKey: keys.targetAllocation, queryFn: api.targetAllocation })

export function useSetTargetAllocation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (target: TargetAllocation) => api.setTargetAllocation(target),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.targetAllocation }),
  })
}

export const useLots = () => useQuery({ queryKey: keys.lots, queryFn: () => api.lots() })

export const useRisk = (range?: DateRange) =>
  useQuery({ queryKey: keys.risk(range), queryFn: () => api.risk(range) })

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
    mutationFn: (settings: HysaSettings) => api.setHysaSettings(settings),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}

export const useBenchmarkSetting = () =>
  useQuery({ queryKey: keys.benchmarkSetting, queryFn: api.benchmarkSetting })

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
