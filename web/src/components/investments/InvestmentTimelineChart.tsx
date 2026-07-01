import { Bar, CartesianGrid, ComposedChart, Line, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { formatDate, formatUsd } from '@/lib/format'
import { useDailyInvestment } from '@/hooks/usePortfolioData'

export function InvestmentTimelineChart() {
  const { data, isLoading } = useDailyInvestment()

  return (
    <ChartCard
      title="Investment timeline"
      description="Amount invested per day, and the running total"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <ComposedChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="trade_date"
          tickFormatter={formatDate}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          yAxisId="daily"
          tickFormatter={(v) => formatUsd(v, true)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <YAxis
          yAxisId="cumulative"
          orientation="right"
          tickFormatter={(v) => formatUsd(v, true)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <Tooltip
          formatter={(value, name) => [
            formatUsd(Number(value)),
            name === 'usd_spent' ? 'Invested that day' : 'Cumulative invested',
          ]}
          labelFormatter={(label) => formatDate(String(label))}
        />
        <Bar yAxisId="daily" dataKey="usd_spent" fill="#93c5fd" radius={2} />
        <Line
          yAxisId="cumulative"
          type="monotone"
          dataKey="cumulative_usd_spent"
          stroke="#0f172a"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ChartCard>
  )
}
