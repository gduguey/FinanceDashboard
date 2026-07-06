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
function buildCurve(
  contributions: GoalContribution[],
  goal: Goal,
  ratesToBase: Record<string, number>,
): CurvePoint[] {
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

  const lastDate = sorted[sorted.length - 1].date.slice(0, 10)
  const targetDateStr = goal.target_date.slice(0, 10)
  if (targetDateStr > lastDate) {
    points.push({ date: targetDateStr, balance: null, benchmark: goal.target_amount })
  }
  return points
}

export function GoalDetailChart({ goal, contributions }: { goal: Goal; contributions: GoalContribution[] }) {
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD')
  const ratesToBase = useRatesToBase(nonBaseCurrencies)
  const points = buildCurve(contributions, goal, ratesToBase)
  const targetDateStr = goal.target_date.slice(0, 10)

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
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">No contributions yet</div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={points} margin={{ left: 8, right: 16, top: 16, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(value: string) => formatDate(value)} minTickGap={40} />
              <YAxis width={64} tickFormatter={(value: number) => formatCurrencyCompact(value, goal.target_currency)} />
              <Tooltip
                formatter={(value, name) => [
                  formatCurrency(Number(value), goal.target_currency),
                  name === 'balance' ? 'Balance' : 'Linear benchmark',
                ]}
                labelFormatter={(label) => formatDate(String(label))}
              />
              {/* Both reference lines' labels are pinned to the top-left of
                  their own line rather than centered on it — the default
                  position sits right on top of the dashed line and, for
                  the target-date line especially (usually near the
                  chart's own right edge), runs past the plot area and
                  gets cropped. */}
              <ReferenceLine
                y={goal.target_amount}
                stroke={goal.color}
                strokeDasharray="4 4"
                label={{ value: 'Target', position: 'insideTopLeft', fill: goal.color, fontSize: 11, dy: -4 }}
              />
              <ReferenceLine
                x={targetDateStr}
                stroke="#94a3b8"
                strokeDasharray="2 2"
                label={{ value: 'Target date', position: 'insideTopLeft', fill: '#64748b', fontSize: 11, dx: -4 }}
              />
              <Line type="monotone" dataKey="benchmark" stroke="#94a3b8" strokeDasharray="4 4" dot={false} strokeWidth={1.5} />
              <Line type="stepAfter" dataKey="balance" stroke={goal.color} strokeWidth={2} dot={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
