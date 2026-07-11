import { LlmUsageBanner } from '@/components/accounting/LlmUsageBanner'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { PageHeader } from '@/components/layout/PageHeader'
import { ExportButtons } from '@/components/shared/ExportButtons'
import { Skeleton } from '@/components/ui/skeleton'
import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'
import { accountingApi } from '@/lib/accountingApi'
import { downloadCsv, downloadJson, exportStamp } from '@/lib/download'

// Was the "Transactions" tab inside the old combined Accounting page,
// promoted to its own top-level page. The transfer-suggestions panel that
// used to live above this table moved to Rules -> Suggestions instead —
// matching transfers are a rules-authoring task, not a transactions-review one.
export function TransactionsPage() {
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Transactions"
        actions={
          <>
            {/* "Postings" is already loaded for the table itself — no
                extra request needed. "Ledger" is the raw, unresolved
                history (before rules, overrides, splits, or merges),
                which the table never fetches on its own, so this one
                does its own request. Both also live on Settings' own
                Export tab, for anyone who'd rather find every export in
                one place. */}
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Postings
              <ExportButtons
                onJson={() => downloadJson(postings ?? [], `postings-${exportStamp()}.json`)}
                onCsv={() => downloadCsv(postings ?? [], `postings-${exportStamp()}.csv`)}
              />
            </div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Ledger
              <ExportButtons
                onJson={async () =>
                  downloadJson(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.json`)
                }
                onCsv={async () =>
                  downloadCsv(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.csv`)
                }
              />
            </div>
          </>
        }
      />

      <div className="mx-auto max-w-7xl space-y-6 px-8 py-8">
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
