import { useMemo, useState } from 'react'
import type { TooltipContentProps } from 'recharts'
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { Button } from '@/components/ui/button'
import { colorForIndex } from '@/lib/colors'
import { formatMonth, formatUsd } from '@/lib/format'
import { useMonthlyPnl, useMonthlyPnlBySymbol } from '@/hooks/usePortfolioData'
import type { MonthlyPnlBySymbol } from '@/types/portfolio'

const CASH_COLOR = '#94a3b8'
const CASH_SYMBOL = 'CASH'

interface PivotedRow {
  month: string
  cash_contribution: number
  cash_gain: number
  cash_total: number
  contribution_total: number
  gain_total: number
  [key: string]: string | number
}

function pivotBySymbol(rows: MonthlyPnlBySymbol[] | undefined) {
  if (!rows?.length) return { data: [] as PivotedRow[], symbols: [] as string[] }
  const months = [...new Set(rows.map((r) => r.month))].sort()
  const symbols = [...new Set(rows.map((r) => r.symbol).filter((s) => s !== CASH_SYMBOL))].sort()
  const byMonth = new Map<string, Partial<PivotedRow>>()
  for (const row of rows) {
    const entry = byMonth.get(row.month) ?? {}
    if (row.symbol === CASH_SYMBOL) {
      entry.cash_contribution = row.contribution_usd
      entry.cash_gain = row.market_gain_usd
    } else {
      entry[`${row.symbol}__contribution`] = row.contribution_usd
      entry[`${row.symbol}__gain`] = row.market_gain_usd
    }
    byMonth.set(row.month, entry)
  }
  const data = months.map((month) => {
    const entry = byMonth.get(month) ?? {}
    const contributionTotal = symbols.reduce((sum, s) => sum + (Number(entry[`${s}__contribution`]) || 0), 0)
    const gainTotal = symbols.reduce((sum, s) => sum + (Number(entry[`${s}__gain`]) || 0), 0)
    const cashTotal = (entry.cash_contribution ?? 0) + (entry.cash_gain ?? 0)
    return {
      month,
      cash_contribution: entry.cash_contribution ?? 0,
      cash_gain: entry.cash_gain ?? 0,
      cash_total: cashTotal,
      contribution_total: contributionTotal,
      gain_total: gainTotal,
      ...entry,
    } as PivotedRow
  })
  return { data, symbols }
}

// A compact, categorized tooltip — the default recharts tooltip lists
// every symbol's contribution AND gain as flat, same-sized lines, which
// becomes unreadable with more than a couple of symbols.
function BySymbolTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload as PivotedRow | undefined
  if (!row) return null
  const symbols = Object.keys(row)
    .filter((key) => key.endsWith('__contribution'))
    .map((key) => key.replace('__contribution', ''))

  return (
    <div className="max-w-56 rounded-md border border-border bg-popover p-2 text-xs shadow-md">
      <div className="mb-1.5 font-medium">{formatMonth(String(label))}</div>
      <div className="mb-1.5">
        <div className="font-medium text-muted-foreground">Cash</div>
        <div className="flex justify-between gap-3">
          <span>Contribution</span>
          <span className="tabular-nums">{formatUsd(row.cash_contribution)}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span>Gain</span>
          <span className="tabular-nums">{formatUsd(row.cash_gain)}</span>
        </div>
      </div>
      <div className="mb-1.5">
        <div className="font-medium text-muted-foreground">Contributions</div>
        {symbols.map((symbol) => (
          <div key={symbol} className="flex justify-between gap-3">
            <span>{symbol}</span>
            <span className="tabular-nums">{formatUsd(Number(row[`${symbol}__contribution`]))}</span>
          </div>
        ))}
      </div>
      <div>
        <div className="font-medium text-muted-foreground">Market gain</div>
        {symbols.map((symbol) => (
          <div key={symbol} className="flex justify-between gap-3">
            <span>{symbol}</span>
            <span className="tabular-nums">{formatUsd(Number(row[`${symbol}__gain`]))}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Contributions in grey, actual market P&L in green/red — directly kills
// the "portfolio is up $12k (of which $11k was my paycheck)" illusion
// every month. The per-symbol toggle breaks each of those two bars down
// by symbol instead, with cash isolated into its own bar (leftmost) since
// it isn't a security's contribution or gain.
export function MonthlyPnlChart() {
  const [bySymbol, setBySymbol] = useState(false)
  const aggregate = useMonthlyPnl()
  const perSymbol = useMonthlyPnlBySymbol()
  const { data: pivoted, symbols } = useMemo(
    () => pivotBySymbol(bySymbol ? perSymbol.data : undefined),
    [bySymbol, perSymbol.data],
  )

  const isLoading = bySymbol ? perSymbol.isLoading : aggregate.isLoading
  const isEmpty = bySymbol ? !pivoted.length : !aggregate.data?.length
  const error = bySymbol ? perSymbol.error : aggregate.error

  return (
    <ChartCard
      title="Monthly contributions vs. market gain"
      titleTooltip="marketGain"
      description="What you put in, separated from what the market actually did"
      isLoading={isLoading}
      isEmpty={isEmpty}
      error={error?.message}
      action={
        <Button variant="outline" size="sm" onClick={() => setBySymbol((v) => !v)}>
          {bySymbol ? 'Show total' : 'Break down by symbol'}
        </Button>
      }
    >
      {bySymbol ? (
        <BarChart data={pivoted} margin={{ left: 8, right: 8, top: 20 }}>
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
          <Tooltip content={BySymbolTooltip} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="cash_contribution" stackId="cash" name="Cash contribution" fill={CASH_COLOR} radius={2} />
          <Bar dataKey="cash_gain" stackId="cash" name="Cash gain" fill={CASH_COLOR} fillOpacity={0.55} radius={2}>
            <TotalLabel dataKey="cash_total" />
          </Bar>
          {symbols.map((symbol, index) => (
            <Bar
              key={`${symbol}-contribution`}
              dataKey={`${symbol}__contribution`}
              stackId="contributions"
              name={`${symbol} contribution`}
              fill={colorForIndex(index)}
              radius={2}
            >
              {index === symbols.length - 1 && <TotalLabel dataKey="contribution_total" />}
            </Bar>
          ))}
          {symbols.map((symbol, index) => (
            <Bar
              key={`${symbol}-gain`}
              dataKey={`${symbol}__gain`}
              stackId="gain"
              name={`${symbol} gain`}
              fill={colorForIndex(index)}
              fillOpacity={0.55}
              radius={2}
            >
              {index === symbols.length - 1 && <TotalLabel dataKey="gain_total" />}
            </Bar>
          ))}
        </BarChart>
      ) : (
        <BarChart data={aggregate.data} margin={{ left: 8, right: 8, top: 8 }}>
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
          <Bar dataKey="contributions_usd" name="Contributions" fill="#94a3b8" radius={2} />
          <Bar dataKey="market_gain_usd" name="Market gain" radius={2}>
            {aggregate.data?.map((point) => (
              <Cell key={point.month} fill={point.market_gain_usd >= 0 ? '#059669' : '#dc2626'} />
            ))}
          </Bar>
        </BarChart>
      )}
    </ChartCard>
  )
}

// Shows the whole stack's total above its topmost segment. Pointing
// `dataKey` at a pre-computed `*_total` field (rather than reading the
// segment's own value) makes recharts resolve it straight off the row, so
// this only needs to place the text — it doesn't reach into `payload`
// itself, which isn't part of what a Bar's label content receives.
function TotalLabel({ dataKey }: { dataKey: keyof PivotedRow }) {
  return (
    <LabelList
      dataKey={dataKey}
      content={(props) => {
        const { value, viewBox } = props as {
          value?: number
          viewBox?: { x: number; y: number; width: number; height: number }
        }
        if (!viewBox || !value || Number.isNaN(value)) return null
        // For negative stacks the bar's rect sits below its own top edge —
        // nudge the label below the rect instead so it stays clear of it.
        const labelY = value < 0 ? viewBox.y + viewBox.height + 14 : viewBox.y - 6
        return (
          <text
            x={viewBox.x + viewBox.width / 2}
            y={labelY}
            textAnchor="middle"
            fontSize={11}
            fill="var(--muted-foreground)"
          >
            {formatUsd(value, true)}
          </text>
        )
      }}
    />
  )
}
