import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { colorForIndex } from '@/lib/colors'
import { formatMonth, formatUsd } from '@/lib/format'
import { useMonthlyInvested } from '@/hooks/usePortfolioData'

export function MonthlyInvestedChart() {
  const { data, isLoading } = useMonthlyInvested()
  const symbols = data?.length
    ? Object.keys(data[0]).filter((key) => key !== 'month' && key !== 'Total')
    : []

  return (
    <ChartCard
      title="Monthly invested"
      description="USD invested per month, by symbol"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <BarChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="month"
          tickFormatter={formatMonth}
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
          formatter={(value) => formatUsd(Number(value))}
          labelFormatter={(label) => formatMonth(String(label))}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {symbols.map((symbol, index) => (
          <Bar key={symbol} dataKey={symbol} stackId="invested" fill={colorForIndex(index)} radius={2} />
        ))}
      </BarChart>
    </ChartCard>
  )
}
