import { CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { formatDate, formatUsd } from '@/lib/format'
import { useDollarChart } from '@/hooks/usePortfolioData'

// NEW_TASKS.md 6.2: the single most valuable addition — contributions
// (moves only on external flows), portfolio market value, and the HYSA +
// benchmark counterfactuals on one chart, so the vertical distances answer
// "what's happening / am I beating cash / is the gap growing" at a glance.
export function DollarChart() {
  const { data, isLoading } = useDollarChart()
  const series = data?.series
  const markers = data?.reallocation_markers ?? []

  return (
    <ChartCard
      title="Portfolio vs. cash and market benchmarks"
      description="Contributions (grey step), your portfolio, HYSA and benchmark counterfactuals"
      isLoading={isLoading}
      isEmpty={!series?.length}
    >
      <LineChart data={series} margin={{ left: 8, right: 8, top: 8 }}>
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
          formatter={(value, name) => [formatUsd(Number(value)), name]}
          labelFormatter={(label) => formatDate(String(label))}
        />
        <Line
          type="stepAfter"
          dataKey="contributions_usd"
          name="Contributions"
          stroke="#94a3b8"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="portfolio_value_usd"
          name="Portfolio value"
          stroke="#0f172a"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="hysa_value_usd"
          name="HYSA counterfactual"
          stroke="#059669"
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="benchmark_value_usd"
          name="Benchmark counterfactual"
          stroke="#2563eb"
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
        />
        {markers.map((marker) => (
          <ReferenceLine
            key={marker.date}
            x={marker.date}
            stroke="#d97706"
            strokeDasharray="2 2"
            label={{
              value: `sold ${marker.sold_symbols.join(', ')} → bought ${marker.bought_symbols.join(', ')}`,
              fontSize: 10,
              fill: '#d97706',
              position: 'top',
            }}
          />
        ))}
      </LineChart>
    </ChartCard>
  )
}
