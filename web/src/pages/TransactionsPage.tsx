import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/layout/PageHeader'
import { LlmUsageBanner } from '@/components/accounting/LlmUsageBanner'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'

// Was the "Transactions" tab inside the old combined Accounting page,
// promoted to its own top-level page. The transfer-suggestions panel that
// used to live above this table moved to Rules -> Suggestions instead —
// matching transfers are a rules-authoring task, not a transactions-review one.
export function TransactionsPage() {
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Transactions" />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            <LlmUsageBanner />
            <TransactionsTab
              postings={postings ?? []}
              accounts={store.accounts}
              categories={store.categories}
              tags={store.tags}
              rules={store.transfer_rules}
            />
          </>
        )}
      </div>
    </div>
  )
}
