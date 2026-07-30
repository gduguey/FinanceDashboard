import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatCurrency, formatCurrencyCompact } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

export interface ProjectionPoint {
  month: number
  balance: number
  contributions_to_date: number
}

/**
 * The simulator's projected balance against what was actually paid in.
 *
 * Lifted out of `SimulatorPage` so the page can defer it: this was the only
 * place in the app that imported recharts directly into a page, which kept the
 * whole charting library on the simulator route's critical path while every
 * other route had it deferred.
 */
export function ProjectionChart({
  points,
  displayCurrency,
}: {
  points: ProjectionPoint[]
  displayCurrency: CurrencyCode
}) {
  return (
    <ResponsiveContainer width="100%" height={288}>
      <LineChart data={points} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="month"
          tickFormatter={(m) => `${Math.round(m / 12)}y`}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tickFormatter={(v) => formatCurrencyCompact(v, displayCurrency)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <Tooltip
          formatter={(value, name) => [
            formatCurrency(Number(value), displayCurrency),
            name === 'balance' ? 'Balance' : 'Contributed',
          ]}
          labelFormatter={(label) => `Month ${label}`}
        />
        <Line type="monotone" dataKey="balance" name="balance" stroke="#0f172a" strokeWidth={2} dot={false} />
        <Line
          type="monotone"
          dataKey="contributions_to_date"
          name="contributions_to_date"
          stroke="#94a3b8"
          strokeWidth={1.5}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
