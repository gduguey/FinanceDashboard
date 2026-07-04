import { Brush, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { formatDate } from '@/lib/format'
import { useGrowthOf100Chart } from '@/hooks/usePortfolioData'
import type { GlossaryTerm } from '@/lib/glossary'

const LEGEND: { label: string; color: string; term: GlossaryTerm; dashed?: boolean }[] = [
  { label: 'Your NAV', color: '#0f172a', term: 'nav' },
  { label: 'Benchmark', color: '#2563eb', term: 'benchmarkIndex' },
  { label: 'HYSA', color: '#059669', term: 'hysaCounterfactual', dashed: true },
  { label: 'CPI', color: '#d97706', term: 'cpi', dashed: true },
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

// NEW_TASKS.md 3.3/6.3: the time-weighted counterpart to the dollar chart —
// everything indexed to 100 at the same start, so your NAV is directly
// comparable to published benchmark numbers with no cashflow matching.
export function GrowthOf100Chart() {
  const { data, isLoading } = useGrowthOf100Chart()

  return (
    <ChartCard
      title="Growth of $100"
      titleTooltip="growthOf100"
      description="Your strategy's quality vs. benchmarks, independent of contribution timing"
      legend={<ChartLegend />}
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
        <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
      </LineChart>
    </ChartCard>
  )
}
