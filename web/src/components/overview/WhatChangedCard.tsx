import { Target, TrendingDown, TrendingUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency, signColor } from '@/lib/format'
import { useAccountingStore, useCategoryTotals, useGoalsSummary } from '@/hooks/useAccountingData'
import type { CategoryTotalRow, CurrencyCode } from '@/types/accounting'

function monthBounds(offsetMonths: number): { start: string; end: string } {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth() + offsetMonths, 1)
  const end = new Date(now.getFullYear(), now.getMonth() + offsetMonths + 1, 0)
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) }
}

// Same grouping `CashflowSankeyChart.groupByCategory` uses — one row per
// (category, subcategory) pair collapsed back into one total per
// top-level category, since a swing in "Dining" as a whole is the useful
// signal, not one for every subcategory that happens to exist.
function totalsByCategory(rows: CategoryTotalRow[]): Map<string, number> {
  const totals = new Map<string, number>()
  for (const row of rows) {
    if (row.classification !== 'expense') continue
    totals.set(row.category_name, (totals.get(row.category_name) ?? 0) + row.amount)
  }
  return totals
}

function biggestSwing(thisMonth: CategoryTotalRow[], lastMonth: CategoryTotalRow[]) {
  const thisTotals = totalsByCategory(thisMonth)
  const lastTotals = totalsByCategory(lastMonth)
  const categoryNames = new Set([...thisTotals.keys(), ...lastTotals.keys()])
  let biggest: { name: string; thisAmount: number; lastAmount: number; delta: number } | null = null
  for (const name of categoryNames) {
    const thisAmount = thisTotals.get(name) ?? 0
    const lastAmount = lastTotals.get(name) ?? 0
    const delta = thisAmount - lastAmount
    if (biggest === null || Math.abs(delta) > Math.abs(biggest.delta)) {
      biggest = { name, thisAmount, lastAmount, delta }
    }
  }
  return biggest
}

// "Here's what's new" rather than the same static charts every visit —
// the one thing that moved the most since last month, and any goal that
// crossed its finish line since then. Both are diffs of numbers this app
// already computes elsewhere (category totals, goal balances); nothing
// new is stored, only compared across two points in time.
export function WhatChangedCard({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const thisMonth = monthBounds(0)
  const lastMonth = monthBounds(-1)
  const { data: thisMonthTotals, isLoading: thisLoading } = useCategoryTotals(
    thisMonth.start,
    thisMonth.end,
    undefined,
    undefined,
    displayCurrency,
  )
  const { data: lastMonthTotals, isLoading: lastLoading } = useCategoryTotals(
    lastMonth.start,
    lastMonth.end,
    undefined,
    undefined,
    displayCurrency,
  )
  const { data: store } = useAccountingStore()
  const { data: goalsNow } = useGoalsSummary(undefined, displayCurrency)
  const { data: goalsAtMonthStart } = useGoalsSummary(thisMonth.start, displayCurrency)

  const isLoading = thisLoading || lastLoading
  const swing = !isLoading && thisMonthTotals && lastMonthTotals ? biggestSwing(thisMonthTotals, lastMonthTotals) : null

  const goalsHitThisMonth = Object.values(store?.goals ?? {}).filter((goal) => {
    const before = goalsAtMonthStart?.balances[goal.goal_id] ?? 0
    const now = goalsNow?.balances[goal.goal_id] ?? 0
    return before < goal.target_amount && now >= goal.target_amount
  })

  const hasContent = (swing && swing.delta !== 0) || goalsHitThisMonth.length > 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">What changed since last month</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : !hasContent ? (
          <p className="text-sm text-muted-foreground">Nothing's moved much since last month.</p>
        ) : (
          <>
            {swing && swing.delta !== 0 && (
              <div className="flex items-center gap-2 text-sm">
                {swing.delta > 0 ? (
                  <TrendingUp className="size-4 shrink-0 text-rose-500" />
                ) : (
                  <TrendingDown className="size-4 shrink-0 text-emerald-500" />
                )}
                <span>
                  <strong>{swing.name}</strong> {swing.delta > 0 ? 'up' : 'down'}{' '}
                  <span className={signColor(-swing.delta)}>
                    {formatCurrency(Math.abs(swing.delta), displayCurrency)}
                  </span>{' '}
                  vs. last month ({formatCurrency(swing.thisAmount, displayCurrency)} vs.{' '}
                  {formatCurrency(swing.lastAmount, displayCurrency)})
                </span>
              </div>
            )}
            {goalsHitThisMonth.map((goal) => (
              <div key={goal.goal_id} className="flex items-center gap-2 text-sm">
                <Target className="size-4 shrink-0 text-emerald-500" />
                <span>
                  <strong>{goal.name}</strong> reached its target (
                  {formatCurrency(goal.target_amount, goal.target_currency)}) this month
                </span>
              </div>
            ))}
          </>
        )}
      </CardContent>
    </Card>
  )
}
