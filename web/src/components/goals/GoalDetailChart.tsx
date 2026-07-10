import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency, formatCurrencyCompact, formatDate } from '@/lib/format'
import { useCurrencies, useRatesToBase } from '@/hooks/useAccountingData'
import type { Goal, GoalContribution } from '@/types/accounting'

interface CurvePoint {
  date: string
  balance: number | null
  benchmark: number
}

// Cumulative balance over time, a horizontal target-amount reference line,
// a vertical target-date marker, and a subtle grey dotted straight-line
// benchmark from the first contribution to (target_date, target_amount) —
// the pace that would get here linearly, for comparison against the real
// (usually lumpier) contribution history. Every contribution keeps its own
// currency at rest (see `models.GoalContribution`) — this always converts
// into the *goal's* own currency before summing, since the evaluation
// curve for a goal reads in its own currency regardless of whichever
// currency the rest of the app happens to be displaying in right now.
function buildCurve(contributions: GoalContribution[], goal: Goal, ratesToBase: Record<string, number>): CurvePoint[] {
  const sorted = contributions
    .filter((c) => c.goal_id === goal.goal_id)
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date))
  if (sorted.length === 0) return []

  const firstDate = new Date(sorted[0].date).getTime()
  const targetDate = new Date(goal.target_date).getTime()
  const totalSpan = Math.max(targetDate - firstDate, 1)

  let running = 0
  const points: CurvePoint[] = sorted.map((c) => {
    running += convertCurrency(c.amount, c.currency, goal.target_currency, ratesToBase)
    const elapsed = new Date(c.date).getTime() - firstDate
    return { date: c.date.slice(0, 10), balance: running, benchmark: goal.target_amount * (elapsed / totalSpan) }
  })

  // The x-axis is a category axis keyed on each point's own `date` string —
  // a `ReferenceLine x={...}` only renders when that exact string is one of
  // the axis's categories, so the target date needs its own point even when
  // it falls between two contributions (a target date already passed, with
  // contributions still landing after it) rather than only at the end.
  const targetDateStr = goal.target_date.slice(0, 10)
  const hasTargetPoint = points.some((point) => point.date === targetDateStr)
  if (!hasTargetPoint) {
    const insertIndex = points.findIndex((point) => point.date > targetDateStr)
    if (insertIndex === -1) {
      points.push({ date: targetDateStr, balance: null, benchmark: goal.target_amount })
    } else {
      // Carry forward the running balance as of just before the target date,
      // matching the step-after line's own semantics, so inserting this
      // point never opens a visual gap in the balance line.
      const priorBalance = insertIndex > 0 ? points[insertIndex - 1].balance : null
      points.splice(insertIndex, 0, { date: targetDateStr, balance: priorBalance, benchmark: goal.target_amount })
    }
  }
  return points
}

// A tiny colored-swatch key for the reference lines, standing in for
// on-chart labels — those used to sit right on top of their own dashed
// line, illegible against it.
function ChartLegend({ goal }: { goal: Goal }) {
  const entries = [
    { color: goal.color, dashed: false, label: 'Balance' },
    { color: goal.color, dashed: true, label: 'Target' },
    { color: '#94a3b8', dashed: false, label: 'Linear benchmark' },
  ]
  return (
    <div className="flex w-32 shrink-0 flex-col gap-2 text-xs text-muted-foreground">
      {entries.map((entry) => (
        <div key={entry.label} className="flex items-center gap-1.5">
          <span
            className="h-0 w-3.5 shrink-0 border-t-2"
            style={{ borderColor: entry.color, borderStyle: entry.dashed ? 'dashed' : 'solid' }}
          />
          {entry.label}
        </div>
      ))}
    </div>
  )
}

export function GoalDetailChart({ goal, contributions }: { goal: Goal; contributions: GoalContribution[] }) {
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD')
  const ratesToBase = useRatesToBase(nonBaseCurrencies)
  const points = buildCurve(contributions, goal, ratesToBase)
  const targetDateStr = goal.target_date.slice(0, 10)
  // A little headroom above the target amount — so its horizontal line
  // sits inside the plot rather than pinned to the very top edge — sized
  // off whichever is bigger, the target or a balance that's overshot it.
  const maxBalance = Math.max(
    0,
    ...points.map((point) => point.balance ?? 0),
    ...points.map((point) => point.benchmark),
  )
  const yMax = Math.max(goal.target_amount, maxBalance) * 1.15

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle style={{ color: goal.color }}>{goal.name}</CardTitle>
        <CardDescription>
          Target {formatCurrency(goal.target_amount, goal.target_currency)} by {formatDate(targetDateStr)}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
            No contributions yet
          </div>
        ) : (
          <div className="flex gap-4">
            <ResponsiveContainer width="100%" height={260} className="flex-1">
              <LineChart data={points} margin={{ left: 8, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="date"
                  tickFormatter={(value: string) => formatDate(value)}
                  minTickGap={40}
                  padding={{ left: 8, right: 24 }}
                />
                <YAxis
                  width={64}
                  domain={[0, yMax]}
                  tickFormatter={(value: number) => formatCurrencyCompact(value, goal.target_currency)}
                />
                <Tooltip
                  formatter={(value, name) => [
                    formatCurrency(Number(value), goal.target_currency),
                    name === 'balance' ? 'Balance' : 'Linear benchmark',
                  ]}
                  labelFormatter={(label) => formatDate(String(label))}
                />
                <Line
                  type="monotone"
                  dataKey="benchmark"
                  stroke="#94a3b8"
                  strokeDasharray="4 4"
                  dot={false}
                  strokeWidth={1.5}
                />
                <Line
                  type="stepAfter"
                  dataKey="balance"
                  stroke={goal.color}
                  strokeWidth={2}
                  dot={false}
                  connectNulls={false}
                />
                {/* Drawn after both `Line`s so it never occludes them, unlike before. */}
                <ReferenceLine y={goal.target_amount} stroke={goal.color} strokeDasharray="4 4" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
            <ChartLegend goal={goal} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
