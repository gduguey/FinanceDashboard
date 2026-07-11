import { DashboardTab } from '@/components/accounting/DashboardTab'
import { PageHeader } from '@/components/layout/PageHeader'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { Skeleton } from '@/components/ui/skeleton'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'

// Spending/income breakdown, filterable by period/account/tag — was the
// "Dashboard" tab inside the old combined Accounting page; promoted to its
// own top-level page since it's a distinct everyday view, not a setting.
export function InsightsPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Insights"
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
          <DashboardTab
            postings={postings ?? []}
            accounts={store.accounts}
            tags={store.tags}
            goals={store.goals}
            goalContributions={store.goal_contributions}
            displayCurrency={displayCurrency}
          />
        )}
      </div>
    </div>
  )
}
