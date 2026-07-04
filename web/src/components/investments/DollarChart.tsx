import { Brush, CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { formatDate, formatUsd } from '@/lib/format'
import { useDollarChart } from '@/hooks/usePortfolioData'
import type { GlossaryTerm } from '@/lib/glossary'

const LEGEND: { label: string; color: string; term: GlossaryTerm; dashed?: boolean }[] = [
  { label: 'Contributions', color: '#94a3b8', term: 'contributions' },
  { label: 'Portfolio value', color: '#0f172a', term: 'portfolioValue' },
  { label: 'HYSA counterfactual', color: '#059669', term: 'hysaCounterfactual', dashed: true },
  { label: 'Benchmark counterfactual', color: '#2563eb', term: 'benchmarkCounterfactual', dashed: true },
]

function ChartLegend() {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {LEGEND.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1">
          <span
            className="inline-block h-0.5 w-3"
            style={{ background: item.color, opacity: item.dashed ? 0.6 : 1 }}
          />
          {item.label}
          <InfoTooltip term={item.term} />
        </span>
      ))}
    </div>
  )
}

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
      description="Contributions, your portfolio, and what the same money would be worth elsewhere"
      legend={<ChartLegend />}
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
        <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
      </LineChart>
    </ChartCard>
  )
}
