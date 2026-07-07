import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/layout/PageHeader'
import { TagsTab } from '@/components/accounting/TagsTab'
import { useAccountingStore } from '@/hooks/useAccountingData'

// Was the "Tags" tab inside the old combined Accounting page, promoted to
// its own top-level page under Setup.
export function TagsPage() {
  const { data: store, isLoading } = useAccountingStore()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Tags" />

      <div className="mx-auto max-w-3xl space-y-6 px-8 py-8">
        {isLoading || !store ? <Skeleton className="h-64 w-full" /> : <TagsTab tags={store.tags} />}
      </div>
    </div>
  )
}
