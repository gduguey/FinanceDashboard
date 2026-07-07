import { useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { Button } from '@/components/ui/button'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { BenchmarkPicker } from '@/components/investments/BenchmarkPicker'
import { HysaSettingsPanel } from '@/components/investments/HysaSettingsPanel'
import { formatDate, formatUsd } from '@/lib/format'
import { benchmarkLabel, hysaLabel } from '@/lib/labels'
import { useBenchmarkSetting, useCashHistory, useHysaRates, useHysaSettings, useTaxSettings } from '@/hooks/usePortfolioData'
import type { GlossaryTerm } from '@/lib/glossary'
import type { CashHistoryPoint } from '@/types/portfolio'

interface DurationPoint {
  percentile: number
  cash: number
}

// A load-duration-curve transform, applied to cash instead of power draw:
// sort every day's balance descending, then plot it against what percentage
// of the days in range held at least that much cash. Answers "how much of
// the time was a meaningful cash pile sitting around," not just "what's the
// balance today" — a account that dips to zero every payday reads very
// differently from one that's flat all year, even with the same average.
function durationCurve(points: CashHistoryPoint[]): DurationPoint[] {
  const sorted = [...points].map((p) => p.cash).sort((a, b) => b - a)
  const n = sorted.length
  if (n === 0) return []
  if (n === 1) return [{ percentile: 100, cash: sorted[0] }]
  return sorted.map((cash, index) => ({ percentile: (index / (n - 1)) * 100, cash }))
}

function buildLegend(benchmarkName: string, hysaName: string, taxAdjusted: boolean) {
  return [
    { label: 'Cash', color: '#0f172a', term: undefined },
    {
      label: `If still sitting, invested in ${benchmarkName}`,
      color: '#2563eb',
      term: 'cashSittingCounterfactual' as GlossaryTerm,
      dashed: true,
    },
    {
      label: `If still sitting, invested at ${hysaName}${taxAdjusted ? ' — after tax' : ''}`,
      color: '#059669',
      term: 'cashSittingCounterfactual' as GlossaryTerm,
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
          <span
            className="inline-block h-0.5 w-3"
            style={{ background: item.color, opacity: item.dashed ? 0.6 : 1 }}
          />
          {item.label}
          {item.term && <InfoTooltip term={item.term} />}
        </span>
      ))}
    </div>
  )
}

export function CashOverTimeChart() {
  const [view, setView] = useState<'time' | 'duration'>('time')
  const { data, isLoading, error } = useCashHistory()
  const { data: benchmarkSetting } = useBenchmarkSetting()
  const { data: hysaSettings } = useHysaSettings()
  const { data: hysaRates } = useHysaRates()
  const { data: taxSettings } = useTaxSettings()
  const duration = useMemo(() => durationCurve(data ?? []), [data])

  const taxAdjusted = taxSettings?.tax_enabled ?? false
  const benchmarkName = benchmarkLabel(benchmarkSetting)
  const hysaName = hysaLabel(hysaSettings, hysaRates)
  const benchmarkLineName = `If still sitting, invested in ${benchmarkName}`
  const hysaLineName = `If still sitting, invested at ${hysaName}${taxAdjusted ? ' — after tax' : ''}`
  const latest = data && data.length > 0 ? data[data.length - 1] : undefined

  return (
    <div className="space-y-2">
      <ChartCard
        title={view === 'time' ? 'Cash over time' : 'Cash duration curve'}
        description={
          view === 'time'
            ? "Uninvested cash balance, day by day — dashed lines show what currently-sitting cash would be worth had it been invested since it arrived"
            : 'Cash amount vs. the percentage of days it stayed at or above that amount'
        }
        legend={
          view === 'time' ? (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-4">
                <BenchmarkPicker />
                <HysaSettingsPanel />
              </div>
              <ChartLegend benchmarkName={benchmarkName} hysaName={hysaName} taxAdjusted={taxAdjusted} />
            </>
          ) : undefined
        }
        isLoading={isLoading}
        isEmpty={!data?.length}
        error={error?.message}
        action={
          <Button variant="outline" size="sm" onClick={() => setView((v) => (v === 'time' ? 'duration' : 'time'))}>
            {view === 'time' ? 'Show duration curve' : 'Show over time'}
          </Button>
        }
      >
        {view === 'time' ? (
          <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
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
            <Line type="stepAfter" dataKey="cash" name="Cash" stroke="#0f172a" strokeWidth={2} dot={false} />
            <Line
              type="monotone"
              dataKey="benchmark_live_usd"
              name={benchmarkLineName}
              stroke="#2563eb"
              strokeWidth={2}
              strokeDasharray="4 4"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="hysa_live_usd"
              name={hysaLineName}
              stroke="#059669"
              strokeWidth={2}
              strokeDasharray="4 4"
              dot={false}
            />
          </LineChart>
        ) : (
          <LineChart data={duration} margin={{ left: 8, right: 8, top: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="percentile"
              type="number"
              domain={[0, 100]}
              tickFormatter={(v) => `${v}%`}
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
              formatter={(value) => [formatUsd(Number(value)), 'Cash']}
              labelFormatter={(label) => `At or above this amount ${Number(label).toFixed(0)}% of the time`}
            />
            <Line type="stepAfter" dataKey="cash" stroke="#0f172a" strokeWidth={2} dot={false} />
          </LineChart>
        )}
      </ChartCard>

      {/* A separate, permanently-growing total — kept off the chart's own
          axes on purpose, since it can dwarf the bounded cash balance
          after a few years and would flatten that line to a sliver. See
          `cashSittingCounterfactual` for why it's frozen rather than
          plotted as a third live line. */}
      {view === 'time' && latest && (
        <p className="px-1 text-xs text-muted-foreground">
          Realized from past sitting episodes:{' '}
          <span className="font-medium text-foreground">{formatUsd(latest.benchmark_realized_usd)}</span> vs.{' '}
          {benchmarkName},{' '}
          <span className="font-medium text-foreground">{formatUsd(latest.hysa_realized_usd)}</span> vs. {hysaName}
          <InfoTooltip term="cashSittingCounterfactual" />
        </p>
      )}
    </div>
  )
}
