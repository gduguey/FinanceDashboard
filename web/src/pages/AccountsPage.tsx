import { AccountsManagementTable } from '@/components/accounting/AccountsManagementTable'
import { PageHeader } from '@/components/layout/PageHeader'
import { Skeleton } from '@/components/ui/skeleton'
import { useAccountingStore } from '@/hooks/useAccountingData'

// Was the accounts table at the bottom of the old Import page, promoted to
// its own top-level page — managing which accounts exist is an everyday
// task in its own right, not a side effect of importing a statement.
//
// Which accounts have their kind and currency locked is the one thing this
// page ever wanted the ledger for, and it fetched every posting to derive a
// `Set` of account ids from it. `store.account_ids_with_postings` is that
// `Set` already made, on a read the page issues anyway, from the same
// predicate `PUT /accounts/{account_id}` enforces the lock with.
export function AccountsPage() {
  const { data: store, isLoading } = useAccountingStore()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Accounts" />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <AccountsManagementTable
            accounts={store.accounts}
            accountIdsWithPostings={new Set(store.account_ids_with_postings)}
          />
        )}
      </div>
    </div>
  )
}
