import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type DateRange } from '@/lib/api'
import type { TargetAllocation } from '@/types/portfolio'

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

export function useSync() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}
