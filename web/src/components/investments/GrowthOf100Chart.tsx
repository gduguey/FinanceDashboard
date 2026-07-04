import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { formatDate } from '@/lib/format'
import { useGrowthOf100Chart } from '@/hooks/usePortfolioData'

// NEW_TASKS.md 3.3/6.3: the time-weighted counterpart to the dollar chart —
// everything indexed to 100 at the same start, so your NAV is directly
// comparable to published benchmark numbers with no cashflow matching.
export function GrowthOf100Chart() {
  const { data, isLoading } = useGrowthOf100Chart()

  return (
    <ChartCard
      title="Growth of $100"
      description="Your strategy's quality vs. benchmarks, independent of contribution timing"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 12 }} axisLine={false} tickLine={false} width={48} />
        <Tooltip
          formatter={(value, name) => [Number(value).toFixed(1), name]}
          labelFormatter={(label) => formatDate(String(label))}
        />
        <Line
          type="monotone"
          dataKey="portfolio_index"
          name="Your NAV"
          stroke="#0f172a"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="benchmark_index"
          name="Benchmark"
          stroke="#2563eb"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="hysa_index"
          name="HYSA"
          stroke="#059669"
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="cpi_index"
          name="CPI"
          stroke="#d97706"
          strokeWidth={2}
          strokeDasharray="2 2"
          dot={false}
          connectNulls
        />
      </LineChart>
    </ChartCard>
  )
}
