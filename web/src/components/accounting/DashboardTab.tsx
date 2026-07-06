import { useMemo } from 'react'
import { PeriodFilterBar, usePeriodFilter } from '@/components/accounting/PeriodFilter'
import { CategoryDrilldownPie } from '@/components/accounting/CategoryDrilldownPie'
import { IncomeExpenseChart } from '@/components/accounting/IncomeExpenseChart'
import { SpendCurveChart } from '@/components/accounting/SpendCurveChart'
import { CashflowSankeyChart, type GoalFlow } from '@/components/accounting/CashflowSankeyChart'
import { useCategoryTotals } from '@/hooks/useAccountingData'
import type { Account, CurrencyCode, Goal, GoalContribution, Posting, Tag } from '@/types/accounting'

export function DashboardTab({
  postings,
  accounts,
  tags,
  goals,
  goalContributions,
  displayCurrency,
}: {
  postings: Posting[]
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
      .map(([goalId, value]) => ({ name: goals[goalId]?.name ?? goalId, value, color: goals[goalId]?.color ?? '#059669' }))
      .filter((flow) => flow.value > 0)
  }, [goalContributions, goals, filter.period])

  const scopedPostings = useMemo(
    () =>
      postings.filter((posting) => {
        const day = posting.posted_at.slice(0, 10)
        if (day < filter.period.start || day > filter.period.end) return false
        if (filter.accountId && posting.account_id !== filter.accountId) return false
        if (filter.tagId && !posting.tag_ids.includes(filter.tagId)) return false
        return true
      }),
    [postings, filter.period, filter.accountId, filter.tagId],
  )

  return (
    <div className="space-y-6">
      <PeriodFilterBar filter={filter} accounts={accounts} tags={tags} postings={postings} />
      <CategoryDrilldownPie
        categoryTotals={categoryTotals ?? []}
        postings={scopedPostings}
        allPostings={postings}
        accounts={accounts}
        isLoading={isLoading}
        displayCurrency={displayCurrency}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <IncomeExpenseChart displayCurrency={displayCurrency} />
        <SpendCurveChart displayCurrency={displayCurrency} postings={postings} />
      </div>
      <CashflowSankeyChart categoryTotals={categoryTotals ?? []} goalFlows={goalFlows} displayCurrency={displayCurrency} />
    </div>
  )
}
