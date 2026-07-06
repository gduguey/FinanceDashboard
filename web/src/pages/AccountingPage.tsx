import { useSearchParams } from 'react-router-dom'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { PageHeader } from '@/components/layout/PageHeader'
import { DashboardTab } from '@/components/accounting/DashboardTab'
import { LlmUsageBanner } from '@/components/accounting/LlmUsageBanner'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { CategoriesTab } from '@/components/accounting/CategoriesTab'
import { TagsTab } from '@/components/accounting/TagsTab'
import { TransferRulesTab } from '@/components/accounting/TransferRulesTab'
import { TransferSuggestionsPanel } from '@/components/accounting/TransferSuggestionsPanel'
import { DuplicateSuggestionsPanel } from '@/components/accounting/DuplicateSuggestionsPanel'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'

export function AccountingPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()
  const [searchParams] = useSearchParams()
  const initialTab = searchParams.get('tab') ?? 'dashboard'

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Accounting"
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
          <Tabs defaultValue={initialTab}>
            <TabsList>
              <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
              <TabsTrigger value="transactions">Transactions</TabsTrigger>
              <TabsTrigger value="categories">Categories</TabsTrigger>
              <TabsTrigger value="tags">Tags</TabsTrigger>
              <TabsTrigger value="transfer-rules">Transfer rules</TabsTrigger>
              <TabsTrigger value="duplicates">Duplicates</TabsTrigger>
            </TabsList>

            <TabsContent value="dashboard">
              <DashboardTab
                postings={postings ?? []}
                accounts={store.accounts}
                tags={store.tags}
                goals={store.goals}
                goalContributions={store.goal_contributions}
                displayCurrency={displayCurrency}
              />
            </TabsContent>

            <TabsContent value="transactions" className="space-y-6">
              <LlmUsageBanner />
              <TransferSuggestionsPanel accounts={store.accounts} rules={store.transfer_rules} />
              <TransactionsTab
                postings={postings ?? []}
                accounts={store.accounts}
                categories={store.categories}
                tags={store.tags}
                rules={store.transfer_rules}
              />
            </TabsContent>

            <TabsContent value="categories">
              <CategoriesTab categories={store.categories} patterns={store.category_patterns} />
            </TabsContent>

            <TabsContent value="tags">
              <TagsTab tags={store.tags} />
            </TabsContent>

            <TabsContent value="transfer-rules">
              <TransferRulesTab rules={store.transfer_rules} accounts={store.accounts} />
            </TabsContent>

            <TabsContent value="duplicates">
              <DuplicateSuggestionsPanel accounts={store.accounts} existingMerges={store.posting_merges} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
