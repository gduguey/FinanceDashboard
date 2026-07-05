import { useMemo, useState } from 'react'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Account } from '@/types/accounting'

export type PeriodGranularity = 'month' | 'range' | 'year'

export interface Period {
  start: string
  end: string
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function monthBounds(monthValue: string): Period {
  const [year, month] = monthValue.split('-').map(Number)
  const start = `${year}-${pad(month)}-01`
  const end = new Date(year, month, 0).toISOString().slice(0, 10)
  return { start, end }
}

function yearBounds(yearValue: string): Period {
  return { start: `${yearValue}-01-01`, end: `${yearValue}-12-31` }
}

const CURRENT_MONTH = new Date().toISOString().slice(0, 7)
const CURRENT_YEAR = String(new Date().getFullYear())

// Drives the accounting dashboard's period + account scope — one control
// shared by the drill-down pie and the spend curve, since both answer
// "what happened in this window" and should always agree on it.
export function usePeriodFilter() {
  const [granularity, setGranularity] = useState<PeriodGranularity>('month')
  const [month, setMonth] = useState(CURRENT_MONTH)
  const [year, setYear] = useState(CURRENT_YEAR)
  const [rangeStart, setRangeStart] = useState(monthBounds(CURRENT_MONTH).start)
  const [rangeEnd, setRangeEnd] = useState(monthBounds(CURRENT_MONTH).end)
  const [accountId, setAccountId] = useState<string | null>(null)

  const period = useMemo<Period>(() => {
    if (granularity === 'month') return monthBounds(month)
    if (granularity === 'year') return yearBounds(year)
    return { start: rangeStart, end: rangeEnd }
  }, [granularity, month, year, rangeStart, rangeEnd])

  return {
    granularity,
    setGranularity,
    month,
    setMonth,
    year,
    setYear,
    rangeStart,
    setRangeStart,
    rangeEnd,
    setRangeEnd,
    accountId,
    setAccountId,
    period,
  }
}

export function PeriodFilterBar({
  filter,
  accounts,
}: {
  filter: ReturnType<typeof usePeriodFilter>
  accounts: Record<string, Account>
}) {
  const accountOptions = Object.values(accounts)
    .filter((account) => !['income_source', 'expense_payee'].includes(account.kind))
    .sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div className="flex flex-wrap items-end gap-2">
      <Select value={filter.granularity} onValueChange={(value) => value && filter.setGranularity(value as PeriodGranularity)}>
        <SelectTrigger size="sm" className="w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="month">Month</SelectItem>
          <SelectItem value="range">Date range</SelectItem>
          <SelectItem value="year">Year</SelectItem>
        </SelectContent>
      </Select>

      {filter.granularity === 'month' && (
        <Input type="month" className="w-36" value={filter.month} onChange={(event) => filter.setMonth(event.target.value)} />
      )}
      {filter.granularity === 'year' && (
        <Input
          type="number"
          className="w-24"
          value={filter.year}
          onChange={(event) => filter.setYear(event.target.value)}
        />
      )}
      {filter.granularity === 'range' && (
        <>
          <Input type="date" className="w-36" value={filter.rangeStart} onChange={(event) => filter.setRangeStart(event.target.value)} />
          <Input type="date" className="w-36" value={filter.rangeEnd} onChange={(event) => filter.setRangeEnd(event.target.value)} />
        </>
      )}

      <Select value={filter.accountId ?? '__all__'} onValueChange={(value) => filter.setAccountId(value === '__all__' ? null : value)}>
        <SelectTrigger size="sm" className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">All accounts</SelectItem>
          {accountOptions.map((account) => (
            <SelectItem key={account.account_id} value={account.account_id}>
              {account.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
