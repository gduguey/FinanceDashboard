import { Brush, CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import {
  useBenchmarkSetting,
  useDollarChart,
  useHysaRates,
  useHysaSettings,
  useTaxSettings,
} from '@/hooks/usePortfolioData'
import { formatDate, formatUsd } from '@/lib/format'
import type { GlossaryTerm } from '@/lib/glossary'
import { benchmarkLabel, hysaLabel } from '@/lib/labels'
import type { DollarChartPoint } from '@/types/portfolio'

function buildLegend(benchmarkName: string, hysaName: string, taxAdjusted: boolean) {
  return [
    { label: 'Contributions', color: '#94a3b8', term: 'contributions' as GlossaryTerm },
    { label: 'Portfolio value', color: '#0f172a', term: 'portfolioValue' as GlossaryTerm },
    {
      label: `HYSA counterfactual (${hysaName})${taxAdjusted ? ' — after tax' : ''}`,
      color: '#059669',
      term: 'hysaCounterfactual' as GlossaryTerm,
      dashed: true,
    },
    {
      label: `Benchmark counterfactual (${benchmarkName})`,
      color: '#2563eb',
      term: 'benchmarkCounterfactual' as GlossaryTerm,
      dashed: true,
    },
  ]
}

function ChartLegend({
  benchmarkName,
  hysaName,
  taxAdjusted,
}: {
  benchmarkName: string
  hysaName: string
  taxAdjusted: boolean
}) {
  const items = buildLegend(benchmarkName, hysaName, taxAdjusted)
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-3" style={{ background: item.color, opacity: item.dashed ? 0.6 : 1 }} />
          {item.label}
          <InfoTooltip term={item.term} />
        </span>
      ))}
    </div>
  )
}

// Contributions (moves only on external flows), portfolio market value,
// and the HYSA + benchmark counterfactuals on one chart, so the vertical
// distances answer "what's happening / am I beating cash / is the gap
// growing" at a glance.
export function DollarChart() {
  const { data, isLoading, error } = useDollarChart()
  const { data: benchmarkSetting } = useBenchmarkSetting()
  const { data: hysaSettings } = useHysaSettings()
  const { data: hysaRates } = useHysaRates()
  const { data: taxSettings } = useTaxSettings()
  const series = data?.series
  const markers = data?.reallocation_markers ?? []

  const taxAdjusted = taxSettings?.tax_enabled ?? false
  const benchmarkName = benchmarkLabel(benchmarkSetting)
  const hysaName = hysaLabel(hysaSettings, hysaRates)
  const hysaLineName = `HYSA counterfactual (${hysaName})${taxAdjusted ? ' — after tax' : ''}`
  const benchmarkLineName = `Benchmark counterfactual (${benchmarkName})`

  return (
    <ChartCard
      title="Portfolio vs. cash and market benchmarks"
      description="Contributions, your portfolio, and what the same money would be worth elsewhere"
      legend={<ChartLegend benchmarkName={benchmarkName} hysaName={hysaName} taxAdjusted={taxAdjusted} />}
      isLoading={isLoading}
      isEmpty={!series?.length}
      error={error?.message}
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
          formatter={(value, name, item) => {
            if (name === hysaLineName) {
              const rate = (item.payload as DollarChartPoint).hysa_rate_pct
              const rateLabel = taxAdjusted ? 'after-tax APY' : 'APY'
              return [`${formatUsd(Number(value))} (${rate.toFixed(2)}% ${rateLabel})`, name]
            }
            return [formatUsd(Number(value)), name]
          }}
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
          name={hysaLineName}
          stroke="#059669"
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="benchmark_value_usd"
          name={benchmarkLineName}
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
