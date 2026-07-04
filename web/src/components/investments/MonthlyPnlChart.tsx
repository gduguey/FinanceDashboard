import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { Button } from '@/components/ui/button'
import { colorForIndex } from '@/lib/colors'
import { formatMonth, formatUsd } from '@/lib/format'
import { useMonthlyPnl, useMonthlyPnlBySymbol } from '@/hooks/usePortfolioData'
import type { MonthlyPnlBySymbol } from '@/types/portfolio'

function pivotBySymbol(rows: MonthlyPnlBySymbol[] | undefined) {
  if (!rows?.length) return { data: [], symbols: [] as string[] }
  const months = [...new Set(rows.map((r) => r.month))].sort()
  const symbols = [...new Set(rows.map((r) => r.symbol))].sort()
  const byMonth = new Map<string, Record<string, number>>()
  for (const row of rows) {
    const entry = byMonth.get(row.month) ?? {}
    entry[`${row.symbol}__contribution`] = row.contribution_usd
    entry[`${row.symbol}__gain`] = row.market_gain_usd
    byMonth.set(row.month, entry)
  }
  const data = months.map((month) => ({ month, ...byMonth.get(month) }))
  return { data, symbols }
}

// NEW_TASKS.md 3.5/6.4: contributions in grey, actual market P&L in
// green/red — directly kills the "portfolio is up $12k (of which $11k was
// my paycheck)" illusion every month. The per-symbol toggle breaks each of
// those two bars down by symbol instead.
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

  return (
    <ChartCard
      title="Monthly contributions vs. market gain"
      titleTooltip="marketGain"
      description="What you put in, separated from what the market actually did"
      isLoading={isLoading}
      isEmpty={isEmpty}
      action={
        <Button variant="outline" size="sm" onClick={() => setBySymbol((v) => !v)}>
          {bySymbol ? 'Show total' : 'Break down by symbol'}
        </Button>
      }
    >
      {bySymbol ? (
        <BarChart data={pivoted} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis
            tickFormatter={(v) => formatUsd(v, true)}
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={64}
          />
          <Tooltip
            formatter={(value, name) => [formatUsd(Number(value)), String(name).replace('__', ' — ')]}
            labelFormatter={(label) => formatMonth(String(label))}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {symbols.map((symbol, index) => (
            <Bar
              key={`${symbol}-contribution`}
              dataKey={`${symbol}__contribution`}
              stackId="contributions"
              name={`${symbol} contribution`}
              fill={colorForIndex(index)}
              radius={2}
            />
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
            />
          ))}
        </BarChart>
      ) : (
        <BarChart data={aggregate.data} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
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
