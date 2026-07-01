import type {
  DailyInvestment,
  MonthlyInvested,
  PieOptions,
  ReturnCurve,
  ReturnRow,
  Summary,
  SyncResult,
  Trade,
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

export const api = {
  summary: () => request<Summary>('/api/summary'),
  trades: () => request<Trade[]>('/api/trades'),
  monthlyInvested: () => request<MonthlyInvested[]>('/api/schedule/monthly'),
  dailyInvestment: () => request<DailyInvestment[]>('/api/schedule/daily'),
  pieBreakdown: () => request<PieOptions>('/api/schedule/pie'),
  returns: () => request<ReturnRow[]>('/api/returns'),
  returnCurve: () => request<ReturnCurve>('/api/returns/curve'),
  sync: () => request<SyncResult>('/api/sync', { method: 'POST' }),
}
