import { useMemo } from 'react'
import type { GoalFlow } from '@/components/accounting/CashflowSankeyChart'
import { PeriodFilterBar } from '@/components/accounting/PeriodFilter'
import { lazyChart } from '@/components/shared/lazyChart'
import { NoAccountsYetBanner } from '@/components/shared/NoAccountsYetBanner'
import { useCategoryTotals } from '@/hooks/useAccountingData'
import { usePeriodFilter } from '@/hooks/usePeriodFilter'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { noPostingFilters } from '@/lib/transactionFilters'
import type { Account, CurrencyCode, Goal, GoalContribution, PostingFilters, Tag } from '@/types/accounting'

// Charts are the heaviest code the app ships. `lazyChart` keeps them off this
// page's critical path and streams them in behind a skeleton.
const CashflowSankeyChart = lazyChart(
  () => import('@/components/accounting/CashflowSankeyChart').then((m) => m.CashflowSankeyChart),
  'h-80 w-full',
)
const CategoryDrilldownPie = lazyChart(() =>
  import('@/components/accounting/CategoryDrilldownPie').then((m) => m.CategoryDrilldownPie),
)
const IncomeExpenseChart = lazyChart(() =>
  import('@/components/accounting/IncomeExpenseChart').then((m) => m.IncomeExpenseChart),
)
const SpendCurveChart = lazyChart(() =>
  import('@/components/accounting/SpendCurveChart').then((m) => m.SpendCurveChart),
)

export function DashboardTab({
  accounts,
  tags,
  goals,
  goalContributions,
  displayCurrency,
}: {
  accounts: Record<string, Account>
  tags: Record<string, Tag>
  goals: Record<string, Goal>
  goalContributions: Record<string, GoalContribution>
  displayCurrency: CurrencyCode
}) {
  const filter = usePeriodFilter('accounting.dashboard-period-filter')
  const accountIds = filter.accountId ? [filter.accountId] : undefined
  const { data: categoryTotals, isLoading } = useCategoryTotals(
    filter.period.start,
    filter.period.end,
    accountIds,
    filter.tagId ?? undefined,
    displayCurrency,
  )

  // Same figures `GoalsOverviewCharts` shows as a pie for a given period —
  // recomputed here from the raw contribution rows (already local, no
  // extra request) rather than calling the goals-summary endpoint twice.
  const goalFlows: GoalFlow[] = useMemo(() => {
    const totals = new Map<string, number>()
    for (const contribution of Object.values(goalContributions)) {
      const day = contribution.date.slice(0, 10)
      if (day < filter.period.start || day > filter.period.end || contribution.amount <= 0) continue
      totals.set(contribution.goal_id, (totals.get(contribution.goal_id) ?? 0) + contribution.amount)
    }
    return [...totals.entries()]
      .map(([goalId, value]) => ({
        name: goals[goalId]?.name ?? goalId,
        value,
        color: goals[goalId]?.color ?? '#059669',
      }))
      .filter((flow) => flow.value > 0)
  }, [goalContributions, goals, filter.period])

  // The period bar's three controls in the wire's own vocabulary, so the
  // drilldown asks the server for its rows with the same window the ring was
  // aggregated over. This used to be a `.filter()` pass over every posting the
  // page held resident, which is what it held them for.
  const drilldownScope: Required<PostingFilters> = useMemo(
    () => ({
      ...noPostingFilters(),
      start: filter.period.start,
      end: filter.period.end,
      account: filter.accountId ?? null,
      tags: filter.tagId ? [filter.tagId] : [],
    }),
    [filter.period, filter.accountId, filter.tagId],
  )

  return (
    <div className="space-y-6">
      {!hasAnyRealAccount(Object.values(accounts)) && <NoAccountsYetBanner />}
      <PeriodFilterBar filter={filter} accounts={accounts} tags={tags} />
      <CategoryDrilldownPie
        categoryTotals={categoryTotals ?? []}
        scope={drilldownScope}
        accounts={accounts}
        isLoading={isLoading}
        displayCurrency={displayCurrency}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <IncomeExpenseChart displayCurrency={displayCurrency} />
        <SpendCurveChart displayCurrency={displayCurrency} />
      </div>
      <CashflowSankeyChart
        categoryTotals={categoryTotals ?? []}
        goalFlows={goalFlows}
        displayCurrency={displayCurrency}
      />
    </div>
  )
}
