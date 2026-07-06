import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { DashboardTab } from '@/components/accounting/DashboardTab'
import { LlmUsageBanner } from '@/components/accounting/LlmUsageBanner'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { CategoriesTab } from '@/components/accounting/CategoriesTab'
import { TagsTab } from '@/components/accounting/TagsTab'
import { RulesTab } from '@/components/accounting/RulesTab'
import { CategoryPatternsTab } from '@/components/accounting/CategoryPatternsTab'
import { TransferSuggestionsPanel } from '@/components/accounting/TransferSuggestionsPanel'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'

export function AccountingPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Accounting</h1>
        <div className="flex items-center gap-2">
          <DisplayCurrencyToggle />
          <ExchangeRateSyncButton />
        </div>
      </div>

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Tabs defaultValue="dashboard">
            <TabsList>
              <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
              <TabsTrigger value="transactions">Transactions</TabsTrigger>
              <TabsTrigger value="categories">Categories</TabsTrigger>
              <TabsTrigger value="tags">Tags</TabsTrigger>
              <TabsTrigger value="rules">Rules</TabsTrigger>
              <TabsTrigger value="category-patterns">Category patterns</TabsTrigger>
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
              <TransferSuggestionsPanel accounts={store.accounts} rules={store.rules} />
              <TransactionsTab
                postings={postings ?? []}
                accounts={store.accounts}
                categories={store.categories}
                tags={store.tags}
                rules={store.rules}
              />
            </TabsContent>

            <TabsContent value="categories">
              <CategoriesTab categories={store.categories} />
            </TabsContent>

            <TabsContent value="tags">
              <TagsTab tags={store.tags} />
            </TabsContent>

            <TabsContent value="rules">
              <RulesTab rules={store.rules} accounts={store.accounts} />
            </TabsContent>

            <TabsContent value="category-patterns">
              <CategoryPatternsTab patterns={store.category_patterns} categories={store.categories} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
