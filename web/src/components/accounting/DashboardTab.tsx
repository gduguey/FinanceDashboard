import { useMemo } from 'react'
import { PeriodFilterBar, usePeriodFilter } from '@/components/accounting/PeriodFilter'
import { CategoryDrilldownPie } from '@/components/accounting/CategoryDrilldownPie'
import { IncomeExpenseChart } from '@/components/accounting/IncomeExpenseChart'
import { SpendCurveChart } from '@/components/accounting/SpendCurveChart'
import { CashflowSankeyChart } from '@/components/accounting/CashflowSankeyChart'
import { useCategoryTotals } from '@/hooks/useAccountingData'
import type { Account, CurrencyCode, Posting, Tag } from '@/types/accounting'

export function DashboardTab({
  postings,
  accounts,
  tags,
  displayCurrency,
}: {
  postings: Posting[]
  accounts: Record<string, Account>
  tags: Record<string, Tag>
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
        isLoading={isLoading}
        displayCurrency={displayCurrency}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <IncomeExpenseChart displayCurrency={displayCurrency} />
        <SpendCurveChart displayCurrency={displayCurrency} postings={postings} />
      </div>
      <CashflowSankeyChart categoryTotals={categoryTotals ?? []} displayCurrency={displayCurrency} />
    </div>
  )
}
