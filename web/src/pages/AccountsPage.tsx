import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/layout/PageHeader'
import { AccountsManagementTable } from '@/components/accounting/AccountsManagementTable'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'

// Was the accounts table at the bottom of the old Import page, promoted to
// its own top-level page — managing which accounts exist is an everyday
// task in its own right, not a side effect of importing a statement.
export function AccountsPage() {
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Accounts" />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <AccountsManagementTable
            accounts={store.accounts}
            accountIdsWithPostings={new Set((postings ?? []).map((posting) => posting.account_id))}
          />
        )}
      </div>
    </div>
  )
}
