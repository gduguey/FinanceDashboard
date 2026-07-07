import { Skeleton } from '@/components/ui/skeleton'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { NoAccountsYetBanner } from '@/components/shared/NoAccountsYetBanner'
import { PageHeader } from '@/components/layout/PageHeader'
import { NetWorthHistoryChart } from '@/components/accounting/NetWorthHistoryChart'
import { CashflowSankeyChart } from '@/components/accounting/CashflowSankeyChart'
import { GoalsOverviewCharts } from '@/components/goals/GoalsOverviewCharts'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useAccountingStore, useCategoryTotals, useGoalsSummary } from '@/hooks/useAccountingData'

function currentMonthBounds(): { start: string; end: string } {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().slice(0, 10)
  return { start, end }
}

// The app's own front door — a handful of already-built charts pulled
// from Net Worth, Budget, and Goals, so the very first thing a user sees
// is a bird's-eye view rather than having to pick a page first. Every
// chart here is the exact same component its own page renders; this page
// adds no new data logic of its own, just a light-touch aggregation.
export function OverviewPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { start, end } = currentMonthBounds()
  const { data: categoryTotals } = useCategoryTotals(start, end, undefined, undefined, displayCurrency)
  const goalsSummary = useGoalsSummary(undefined, displayCurrency)

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Overview"
        actions={
          <>
            <DisplayCurrencyToggle />
            <ExchangeRateSyncButton />
          </>
        }
      />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            {!hasAnyRealAccount(Object.values(store.accounts)) && <NoAccountsYetBanner />}
            <NetWorthHistoryChart displayCurrency={displayCurrency} />
            <CashflowSankeyChart
              categoryTotals={categoryTotals ?? []}
              displayCurrency={displayCurrency}
              title="This month's cash flow"
            />
            {Object.keys(store.goals).length > 0 && (
              <GoalsOverviewCharts
                goals={Object.values(store.goals)}
                balances={goalsSummary.data?.balances ?? {}}
                unallocated={goalsSummary.data?.unallocated ?? 0}
                targets={Object.fromEntries(Object.values(store.goals).map((goal) => [goal.goal_id, goal.target_amount]))}
                mode="all_time"
                displayCurrency={displayCurrency}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
