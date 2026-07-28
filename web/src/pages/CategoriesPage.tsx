import { CategoriesTab } from '@/components/accounting/CategoriesTab'
import { PageHeader } from '@/components/layout/PageHeader'
import { Skeleton } from '@/components/ui/skeleton'
import { useAccountingStore } from '@/hooks/useAccountingData'

// Was the "Categories" tab inside the old combined Accounting page,
// promoted to its own top-level page under Setup — configure-occasionally
// taxonomy, not an everyday view.
export function CategoriesPage() {
  const { data: store, isLoading } = useAccountingStore()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Categories" />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <CategoriesTab categories={store.categories} patterns={store.category_patterns} />
        )}
      </div>
    </div>
  )
}
