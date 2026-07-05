import { Brush, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { formatCurrency, formatDate } from '@/lib/format'
import { useNetWorthHistory } from '@/hooks/useAccountingData'
import type { CurrencyCode } from '@/types/accounting'

function startOfHistoryWindow(): string {
  const date = new Date()
  date.setFullYear(date.getFullYear() - 1)
  return date.toISOString().slice(0, 10)
}

const TODAY = new Date().toISOString().slice(0, 10)

export function NetWorthHistoryChart({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const { data, isLoading } = useNetWorthHistory(startOfHistoryWindow(), TODAY, 7, displayCurrency)

  return (
    <ChartCard
      title="Net worth over time"
      description="Last 12 months — drag the handles below the chart to zoom into a range"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis
          tickFormatter={(v) => formatCurrency(v, displayCurrency)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={72}
        />
        <Tooltip
          formatter={(value) => [formatCurrency(Number(value), displayCurrency), 'Net worth']}
          labelFormatter={(label) => formatDate(String(label))}
        />
        <Line type="monotone" dataKey="net_worth" name="Net worth" stroke="#0f172a" strokeWidth={2} dot={false} />
        <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
      </LineChart>
    </ChartCard>
  )
}
