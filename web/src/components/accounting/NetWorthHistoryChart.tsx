import { useMemo, useState } from 'react'
import { Brush, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { colorForIndex } from '@/lib/colors'
import { formatCurrency, formatCurrencyCompact, formatDate } from '@/lib/format'
import { useNetWorthHistory, useNetWorthHistoryByAccount } from '@/hooks/useAccountingData'
import type { CurrencyCode, NetWorthHistoryByAccountPoint } from '@/types/accounting'

function startOfHistoryWindow(): string {
  const date = new Date()
  date.setFullYear(date.getFullYear() - 1)
  return date.toISOString().slice(0, 10)
}

const TODAY = new Date().toISOString().slice(0, 10)

// Pivots the one-row-per-(date, account) series from the API into one row
// per date with a column per account — what `<Line dataKey>` needs to plot
// each account as its own series on a shared time axis.
function pivotByAccount(rows: NetWorthHistoryByAccountPoint[]) {
  const accountNames = new Map<string, string>()
  const byDate = new Map<string, Record<string, number>>()
  for (const row of rows) {
    accountNames.set(row.account_id, row.account_name)
    const entry = byDate.get(row.date) ?? {}
    entry[row.account_id] = row.balance
    byDate.set(row.date, entry)
  }
  const dates = [...byDate.keys()].sort()
  const data = dates.map((date) => ({ date, ...byDate.get(date) }))
  const accounts = [...accountNames.entries()]
    .map(([id, name], index) => ({ id, name, color: colorForIndex(index) }))
    .sort((a, b) => a.name.localeCompare(b.name))
  return { data, accounts }
}

// `compact` drops the detailed-by-account toggle and the zoom brush,
// and shrinks the plot area — for a small trend glance (Overview's health
// strip) rather than the full drill-down experience the Net Worth page wants.
export function NetWorthHistoryChart({
  displayCurrency,
  compact = false,
}: {
  displayCurrency: CurrencyCode
  compact?: boolean
}) {
  const [detailed, setDetailed] = useState(false)
  const [hiddenAccountIds, setHiddenAccountIds] = useState<Set<string>>(new Set())
  const start = startOfHistoryWindow()

  // Daily granularity, like `trades`' own value-over-time charts — a
  // weekly sample would smear over exactly the kind of single-day jump
  // (a big transfer, a statement import) this chart exists to show.
  const aggregate = useNetWorthHistory(start, TODAY, 1, displayCurrency)
  const byAccount = useNetWorthHistoryByAccount(start, TODAY, 1, displayCurrency, detailed && !compact)
  const isLoading = detailed && !compact ? byAccount.isLoading : aggregate.isLoading

  const { data: detailedData, accounts } = useMemo(() => pivotByAccount(byAccount.data ?? []), [byAccount.data])

  function toggleAccount(id: string) {
    setHiddenAccountIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const showDetailed = detailed && !compact
  const isEmpty = showDetailed ? !detailedData.length : !aggregate.data?.length
  const error = showDetailed ? byAccount.error : aggregate.error
  const chartData = showDetailed ? detailedData : (aggregate.data ?? [])
  const plotHeight = compact ? 96 : 288

  return (
    <Card className="gap-3">
      {!compact && (
        <CardHeader>
          <CardTitle>Net worth over time</CardTitle>
          <CardDescription>
            Last 12 months, daily — drag the handles below the chart to zoom into a range
          </CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" onClick={() => setDetailed((prev) => !prev)}>
              {detailed ? 'Show total' : 'Detailed'}
            </Button>
          </CardAction>
        </CardHeader>
      )}
      <CardContent className={compact ? 'p-0' : undefined}>
        {isLoading ? (
          <Skeleton className={compact ? 'h-24 w-full' : 'h-72 w-full'} />
        ) : isEmpty ? (
          <div
            className={`flex items-center justify-center px-6 text-center text-sm text-muted-foreground ${compact ? 'h-24' : 'h-72'}`}
          >
            {error?.message || 'No data yet'}
          </div>
        ) : (
          <div className="flex gap-4">
            <ResponsiveContainer width="100%" height={plotHeight} className="flex-1">
              <LineChart data={chartData} margin={{ left: 8, right: 8, top: 8 }}>
                {!compact && <CartesianGrid vertical={false} stroke="var(--border)" />}
                <XAxis
                  dataKey="date"
                  tickFormatter={formatDate}
                  tick={compact ? false : { fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  hide={compact}
                />
                <YAxis
                  tickFormatter={(v) => formatCurrencyCompact(v, displayCurrency)}
                  tick={compact ? false : { fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  width={compact ? 0 : 64}
                  hide={compact}
                />
                <Tooltip
                  formatter={(value, name) => [
                    formatCurrency(Number(value), displayCurrency),
                    showDetailed ? (accounts.find((a) => a.id === name)?.name ?? name) : 'Net worth',
                  ]}
                  labelFormatter={(label) => formatDate(String(label))}
                />
                {showDetailed ? (
                  accounts
                    .filter((account) => !hiddenAccountIds.has(account.id))
                    .map((account) => (
                      <Line
                        key={account.id}
                        type="monotone"
                        dataKey={account.id}
                        name={account.id}
                        stroke={account.color}
                        strokeWidth={2}
                        dot={false}
                        connectNulls
                      />
                    ))
                ) : (
                  <Line type="monotone" dataKey="net_worth" name="Net worth" stroke="#0f172a" strokeWidth={2} dot={false} />
                )}
                {/* Explicit start/end (rather than relying on Brush's own
                    default) so the initial zoom always spans the entire
                    fetched window — the last year, ending today — instead
                    of whatever Brush would otherwise pick on its own; the
                    `key` resets that selection when the underlying series
                    actually changes (aggregate vs. detailed) rather than
                    preserving a now-stale range. Skipped in compact mode —
                    a glance-sized chart has no room to zoom into anyway. */}
                {!compact && (
                  <Brush
                    key={chartData.length}
                    dataKey="date"
                    height={20}
                    tickFormatter={formatDate}
                    stroke="#94a3b8"
                    travellerWidth={8}
                    startIndex={0}
                    endIndex={chartData.length - 1}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
            {showDetailed && (
              <ul className="flex w-48 shrink-0 flex-col gap-1 overflow-y-auto text-sm">
                {accounts.map((account) => {
                  const hidden = hiddenAccountIds.has(account.id)
                  return (
                    <li key={account.id}>
                      <button
                        type="button"
                        onClick={() => toggleAccount(account.id)}
                        className={`flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-left hover:bg-muted ${hidden ? 'opacity-40' : ''}`}
                      >
                        <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: account.color }} />
                        <span className="flex-1 truncate">{account.name}</span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
