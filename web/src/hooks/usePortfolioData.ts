import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

// One query key per endpoint, grouped under a shared "portfolio" root so a
// single invalidate (see useSync below) refreshes every panel at once.
const keys = {
  summary: ['portfolio', 'summary'],
  trades: ['portfolio', 'trades'],
  monthly: ['portfolio', 'schedule', 'monthly'],
  daily: ['portfolio', 'schedule', 'daily'],
  pie: ['portfolio', 'schedule', 'pie'],
  returns: ['portfolio', 'returns'],
  returnCurve: ['portfolio', 'returns', 'curve'],
} as const

export const useSummary = () => useQuery({ queryKey: keys.summary, queryFn: api.summary })
export const useTrades = () => useQuery({ queryKey: keys.trades, queryFn: api.trades })
export const useMonthlyInvested = () =>
  useQuery({ queryKey: keys.monthly, queryFn: api.monthlyInvested })
export const useDailyInvestment = () =>
  useQuery({ queryKey: keys.daily, queryFn: api.dailyInvestment })
export const usePieBreakdown = () => useQuery({ queryKey: keys.pie, queryFn: api.pieBreakdown })
export const useReturns = () => useQuery({ queryKey: keys.returns, queryFn: api.returns })
export const useReturnCurve = () =>
  useQuery({ queryKey: keys.returnCurve, queryFn: api.returnCurve })

export function useSync() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolio'] }),
  })
}
