import type { ComponentProps } from 'react'
import type { NetWorthHistoryChart as NetWorthHistoryChartComponent } from '@/components/accounting/NetWorthHistoryChart'
import { lazyChart } from '@/components/shared/lazyChart'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useBudgetComparison, useGoalsSummary } from '@/hooks/useAccountingData'
import { formatCurrency } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

// The fourth chart on the overview route, and the one least obviously a chart
// — a compact sparkline inside a stat card. Left static it alone would have
// kept all of recharts on the landing's critical path, undoing the other
// three. See `lazyChart`.
const NetWorthHistoryChart = lazyChart<ComponentProps<typeof NetWorthHistoryChartComponent>>(
  () => import('@/components/accounting/NetWorthHistoryChart').then((m) => m.NetWorthHistoryChart),
  'h-28 w-full',
)

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

// Fraction of the current calendar month already elapsed — the same
// denominator "pace" is measured against, so a budget only reads as
// "ahead of pace" once spend has actually outrun how far the month has
// gotten, not just because it's non-zero on day 3.
function monthProgress(): number {
  const now = new Date()
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  return now.getDate() / daysInMonth
}

function StatCard({
  label,
  value,
  detail,
  detailColor,
}: {
  label: string
  value: string
  detail: string
  detailColor: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">{value}</div>
        <div className={`mt-1 text-xs ${detailColor}`}>{detail}</div>
      </CardContent>
    </Card>
  )
}

// The "how am I doing, right now" strip: a shrunk net-worth trend next to
// the two numbers worth a glance every single visit — whether this
// month's spending is outrunning the month itself, and whether goals have
// collectively promised out more money than actually exists to back them.
export function FinancialHealthStrip({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const month = currentMonth()
  const { data: budgetRows, isLoading: budgetLoading } = useBudgetComparison(month, displayCurrency)
  const { data: goalsSummary, isLoading: goalsLoading } = useGoalsSummary(undefined, displayCurrency)

  const totalBudgeted = (budgetRows ?? []).reduce((sum, row) => sum + row.budgeted, 0)
  const totalActual = (budgetRows ?? []).reduce((sum, row) => sum + row.actual, 0)
  const paceFraction = monthProgress()
  const spendFraction = totalBudgeted > 0 ? totalActual / totalBudgeted : 0
  // A little slack (10 points) before calling it "ahead of pace" — day-to-day
  // noise (rent landing on the 1st, a big grocery run) shouldn't flip this
  // every time it's checked.
  const aheadOfPace = totalBudgeted > 0 && spendFraction > paceFraction + 0.1

  const unallocated = goalsSummary?.unallocated
  const unallocatedNegative = unallocated !== undefined && unallocated < 0

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <NetWorthHistoryChart displayCurrency={displayCurrency} compact />

      {budgetLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : totalBudgeted > 0 ? (
        <StatCard
          label="This month's spend vs. budget pace"
          value={formatCurrency(totalActual, displayCurrency)}
          detail={
            aheadOfPace
              ? `Ahead of pace — ${Math.round(spendFraction * 100)}% of ${formatCurrency(totalBudgeted, displayCurrency)} spent, ${Math.round(paceFraction * 100)}% through the month`
              : `On pace — ${Math.round(spendFraction * 100)}% of ${formatCurrency(totalBudgeted, displayCurrency)} spent, ${Math.round(paceFraction * 100)}% through the month`
          }
          detailColor={aheadOfPace ? 'text-amber-600 dark:text-amber-500' : 'text-muted-foreground'}
        />
      ) : (
        <StatCard
          label="This month's spend"
          value={formatCurrency(totalActual, displayCurrency)}
          detail="No budget set for this month"
          detailColor="text-muted-foreground"
        />
      )}

      {goalsLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : (
        <StatCard
          label="Unallocated goal money"
          value={formatCurrency(unallocated ?? 0, displayCurrency)}
          detail={
            unallocatedNegative ? 'Negative — goals have claimed more than what exists' : 'Not yet assigned to any goal'
          }
          detailColor={unallocatedNegative ? 'text-rose-600 dark:text-rose-400' : 'text-muted-foreground'}
        />
      )}
    </div>
  )
}
