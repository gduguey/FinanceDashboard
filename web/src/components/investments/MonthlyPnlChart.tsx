import { Bar, BarChart, CartesianGrid, Cell, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { formatMonth, formatUsd } from '@/lib/format'
import { useMonthlyPnl } from '@/hooks/usePortfolioData'

// NEW_TASKS.md 3.5/6.4: contributions in grey, actual market P&L in
// green/red — directly kills the "portfolio is up $12k (of which $11k was
// my paycheck)" illusion every month.
export function MonthlyPnlChart() {
  const { data, isLoading } = useMonthlyPnl()

  return (
    <ChartCard
      title="Monthly contributions vs. market gain"
      description="What you put in, separated from what the market actually did"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <BarChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis
          tickFormatter={(v) => formatUsd(v, true)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <Tooltip
          formatter={(value) => formatUsd(Number(value))}
          labelFormatter={(label) => formatMonth(String(label))}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="contributions_usd" name="Contributions" fill="#94a3b8" radius={2} />
        <Bar dataKey="market_gain_usd" name="Market gain" radius={2}>
          {data?.map((point) => (
            <Cell key={point.month} fill={point.market_gain_usd >= 0 ? '#059669' : '#dc2626'} />
          ))}
        </Bar>
      </BarChart>
    </ChartCard>
  )
}
