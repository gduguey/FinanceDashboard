import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ExcludedFromRulesTab } from '@/components/accounting/ExcludedFromRulesTab'
import { ManualTransfersTab } from '@/components/accounting/ManualTransfersTab'
import { TransferRulesTab } from '@/components/accounting/TransferRulesTab'
import { TransferSuggestionsPanel } from '@/components/accounting/TransferSuggestionsPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAccountingStore, useTransactionLegs } from '@/hooks/useAccountingData'
import { realLegByTransactionId } from '@/lib/transferRowInfo'

// Was the "Transfer rules" tab inside the old combined Accounting page,
// promoted to its own top-level page under Setup. The transfer-suggestions
// panel that used to sit above Transactions moved here as its own
// Suggestions tab — matching transfers into a rule is authoring a rule,
// not reviewing a transaction.
export function RulesPage() {
  const { data: store, isLoading } = useAccountingStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'rules'

  // Three of the four tabs render "one transaction as a small card" for a
  // set the user authored — the transactions a rule links, the manual pairs,
  // and the ones excluded from a rule — and each knows nothing but the id.
  // The union of those ids is asked for once here rather than per tab, so
  // switching tabs costs no request and the three cannot disagree. Bounded
  // by what the user has actually authored, and by `PAGE_LIMIT_MAX` on the
  // endpoint. This page used to fetch the entire ledger for the same lookup.
  const transactionIds = useMemo(() => {
    const ids = new Set<string>()
    for (const link of store?.transfer_links ?? []) {
      ids.add(link.transaction_id_a)
      ids.add(link.transaction_id_b)
    }
    for (const rule of store?.transfer_rules ?? []) {
      for (const transactionId of rule.excluded_transaction_ids ?? []) ids.add(transactionId)
    }
    return [...ids]
  }, [store])
  const { data: legs } = useTransactionLegs(transactionIds)
  const legByTransactionId = useMemo(() => realLegByTransactionId(legs ?? {}, store?.accounts ?? {}), [legs, store])

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
              <TabsTrigger value="manual">Manually added transfers</TabsTrigger>
              <TabsTrigger value="excluded">Excluded from rules</TabsTrigger>
            </TabsList>
            <TabsContent value="rules">
              <TransferRulesTab
                rules={store.transfer_rules}
                accounts={store.accounts}
                legByTransactionId={legByTransactionId}
                transferLinks={store.transfer_links}
              />
            </TabsContent>
            <TabsContent value="suggestions">
              {/* The one tab that needs no lookup: a suggestion carries both
                  of its transaction ids, which is all the "link this pair"
                  action takes. */}
              <TransferSuggestionsPanel accounts={store.accounts} rules={store.transfer_rules} />
            </TabsContent>
            <TabsContent value="manual">
              <ManualTransfersTab transferLinks={store.transfer_links} legByTransactionId={legByTransactionId} />
            </TabsContent>
            <TabsContent value="excluded">
              <ExcludedFromRulesTab
                rules={store.transfer_rules}
                accounts={store.accounts}
                legByTransactionId={legByTransactionId}
              />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
