import { useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { Button } from '@/components/ui/button'
import { formatDate, formatUsd } from '@/lib/format'
import { useCashHistory } from '@/hooks/usePortfolioData'
import type { CashHistoryPoint } from '@/types/portfolio'

interface DurationPoint {
  percentile: number
  cash: number
}

// A load-duration-curve transform, applied to cash instead of power draw:
// sort every day's balance descending, then plot it against what percentage
// of the days in range held at least that much cash. Answers "how much of
// the time was a meaningful cash pile sitting around," not just "what's the
// balance today" — a account that dips to zero every payday reads very
// differently from one that's flat all year, even with the same average.
function durationCurve(points: CashHistoryPoint[]): DurationPoint[] {
  const sorted = [...points].map((p) => p.cash).sort((a, b) => b - a)
  const n = sorted.length
  if (n === 0) return []
  if (n === 1) return [{ percentile: 100, cash: sorted[0] }]
  return sorted.map((cash, index) => ({ percentile: (index / (n - 1)) * 100, cash }))
}

export function CashOverTimeChart() {
  const [view, setView] = useState<'time' | 'duration'>('time')
  const { data, isLoading, error } = useCashHistory()
  const duration = useMemo(() => durationCurve(data ?? []), [data])

  return (
    <ChartCard
      title={view === 'time' ? 'Cash over time' : 'Cash duration curve'}
      description={
        view === 'time'
          ? 'Uninvested cash balance, day by day'
          : 'Cash amount vs. the percentage of days it stayed at or above that amount'
      }
      isLoading={isLoading}
      isEmpty={!data?.length}
      error={error?.message}
      action={
        <Button variant="outline" size="sm" onClick={() => setView((v) => (v === 'time' ? 'duration' : 'time'))}>
          {view === 'time' ? 'Show duration curve' : 'Show over time'}
        </Button>
      }
    >
      {view === 'time' ? (
        <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis
            tickFormatter={(v) => formatUsd(v, true)}
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={64}
          />
          <Tooltip
            formatter={(value) => [formatUsd(Number(value)), 'Cash']}
            labelFormatter={(label) => formatDate(String(label))}
          />
          <Line type="stepAfter" dataKey="cash" stroke="#0f172a" strokeWidth={2} dot={false} />
        </LineChart>
      ) : (
        <LineChart data={duration} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="percentile"
            type="number"
            domain={[0, 100]}
            tickFormatter={(v) => `${v}%`}
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v) => formatUsd(v, true)}
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={64}
          />
          <Tooltip
            formatter={(value) => [formatUsd(Number(value)), 'Cash']}
            labelFormatter={(label) => `At or above this amount ${Number(label).toFixed(0)}% of the time`}
          />
          <Line type="stepAfter" dataKey="cash" stroke="#0f172a" strokeWidth={2} dot={false} />
        </LineChart>
      )}
    </ChartCard>
  )
}
