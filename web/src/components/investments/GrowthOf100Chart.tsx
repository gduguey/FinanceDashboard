import { Brush, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { formatDate } from '@/lib/format'
import { benchmarkLabel, hysaLabel } from '@/lib/labels'
import { useBenchmarkSetting, useGrowthOf100Chart, useHysaRates, useHysaSettings, useTaxSettings } from '@/hooks/usePortfolioData'
import type { GlossaryTerm } from '@/lib/glossary'
import type { GrowthOf100Point } from '@/types/portfolio'

function buildLegend(benchmarkName: string, hysaName: string, taxAdjusted: boolean) {
  return [
    { label: 'Your NAV', color: '#0f172a', term: 'nav' as GlossaryTerm },
    { label: `Benchmark (${benchmarkName})`, color: '#2563eb', term: 'benchmarkIndex' as GlossaryTerm },
    {
      label: `HYSA (${hysaName})${taxAdjusted ? ' — after tax' : ''}`,
      color: '#059669',
      term: 'hysaCounterfactual' as GlossaryTerm,
      dashed: true,
    },
    { label: 'CPI', color: '#d97706', term: 'cpi' as GlossaryTerm, dashed: true },
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

// The time-weighted counterpart to the dollar chart — everything indexed
// to 100 at the same start, so your NAV is directly comparable to
// published benchmark numbers with no cashflow matching.
export function GrowthOf100Chart() {
  const { data, isLoading, error } = useGrowthOf100Chart()
  const { data: benchmarkSetting } = useBenchmarkSetting()
  const { data: hysaSettings } = useHysaSettings()
  const { data: hysaRates } = useHysaRates()
  const { data: taxSettings } = useTaxSettings()

  const taxAdjusted = taxSettings?.tax_enabled ?? false
  const benchmarkName = benchmarkLabel(benchmarkSetting)
  const hysaName = hysaLabel(hysaSettings, hysaRates)
  const benchmarkLineName = `Benchmark (${benchmarkName})`
  const hysaLineName = `HYSA (${hysaName})${taxAdjusted ? ' — after tax' : ''}`

  return (
    <ChartCard
      title="Growth of $100"
      titleTooltip="growthOf100"
      description="Your strategy's quality vs. benchmarks, independent of contribution timing"
      legend={<ChartLegend benchmarkName={benchmarkName} hysaName={hysaName} taxAdjusted={taxAdjusted} />}
      isLoading={isLoading}
      isEmpty={!data?.length}
      error={error?.message}
    >
      <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis domain={['auto', 'auto']} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} width={48} />
        <Tooltip
          formatter={(value, name, item) => {
            const formatted = Number(value).toFixed(1)
            if (name === hysaLineName) {
              const rate = (item.payload as GrowthOf100Point).hysa_rate_pct
              const rateLabel = taxAdjusted ? 'after-tax APY' : 'APY'
              return [rate === null ? formatted : `${formatted} (${rate.toFixed(2)}% ${rateLabel})`, name]
            }
            return [formatted, name]
          }}
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
          name={benchmarkLineName}
          stroke="#2563eb"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="hysa_index"
          name={hysaLineName}
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
