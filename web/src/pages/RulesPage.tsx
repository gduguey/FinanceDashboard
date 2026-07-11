import { useSearchParams } from 'react-router-dom'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeader } from '@/components/layout/PageHeader'
import { TransferRulesTab } from '@/components/accounting/TransferRulesTab'
import { TransferSuggestionsPanel } from '@/components/accounting/TransferSuggestionsPanel'
import { useAccountingStore } from '@/hooks/useAccountingData'

// Was the "Transfer rules" tab inside the old combined Accounting page,
// promoted to its own top-level page under Setup. The transfer-suggestions
// panel that used to sit above Transactions moved here as its own
// Suggestions tab — matching transfers into a rule is authoring a rule,
// not reviewing a transaction.
export function RulesPage() {
  const { data: store, isLoading } = useAccountingStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'rules'

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Rules" />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Tabs value={tab} onValueChange={(value) => setSearchParams(value === 'rules' ? {} : { tab: value })}>
            <TabsList>
              <TabsTrigger value="rules">Rules</TabsTrigger>
              <TabsTrigger value="suggestions">Suggestions</TabsTrigger>
            </TabsList>
            <TabsContent value="rules">
              <TransferRulesTab rules={store.transfer_rules} accounts={store.accounts} />
            </TabsContent>
            <TabsContent value="suggestions">
              <TransferSuggestionsPanel accounts={store.accounts} rules={store.transfer_rules} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
