import { useMemo } from 'react'
import { usePersistedState } from '@/hooks/usePersistedState'

export type PeriodGranularity = 'month' | 'range' | 'year'

export interface Period {
  start: string
  end: string
}

interface PeriodFilterState {
  granularity: PeriodGranularity
  month: string
  year: string
  rangeStart: string
  rangeEnd: string
  accountId: string | null
  tagId: string | null
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

// Local calendar, not `toISOString()` (which is UTC): a user near a month/year
// boundary in a negative-UTC timezone should default to *their* current month,
// and CURRENT_YEAR already uses the local `getFullYear()` — keep them consistent.
const _now = new Date()
const CURRENT_MONTH = `${_now.getFullYear()}-${pad(_now.getMonth() + 1)}`
const CURRENT_YEAR = String(_now.getFullYear())

function defaultState(): PeriodFilterState {
  return {
    granularity: 'month',
    month: CURRENT_MONTH,
    year: CURRENT_YEAR,
    rangeStart: monthBounds(CURRENT_MONTH).start,
    rangeEnd: monthBounds(CURRENT_MONTH).end,
    accountId: null,
    tagId: null,
  }
}

// Drives the accounting dashboard's period + account + tag scope — one
// control shared by the drill-down pie and the spend curve, since both
// answer "what happened in this window" and should always agree on it.
// Persisted (see `usePersistedState`) so navigating to another tab or page
// and back doesn't reset it.
export function usePeriodFilter(storageKey: string) {
  const [state, setState] = usePersistedState<PeriodFilterState>(storageKey, defaultState())

  function update(patch: Partial<PeriodFilterState>) {
    setState({ ...state, ...patch })
  }

  const period = useMemo<Period>(() => {
    if (state.granularity === 'month') return monthBounds(state.month)
    if (state.granularity === 'year') return yearBounds(state.year)
    return { start: state.rangeStart, end: state.rangeEnd }
  }, [state.granularity, state.month, state.year, state.rangeStart, state.rangeEnd])

  return {
    ...state,
    setGranularity: (granularity: PeriodGranularity) => update({ granularity }),
    setMonth: (month: string) => update({ month }),
    setYear: (year: string) => update({ year }),
    setRangeStart: (rangeStart: string) => update({ rangeStart }),
    setRangeEnd: (rangeEnd: string) => update({ rangeEnd }),
    setAccountId: (accountId: string | null) => update({ accountId }),
    setTagId: (tagId: string | null) => update({ tagId }),
    reset: () => setState(defaultState()),
    period,
  }
}
