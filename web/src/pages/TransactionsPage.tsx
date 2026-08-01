import { toast } from 'sonner'
import { LlmUsageBanner } from '@/components/accounting/LlmUsageBanner'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { PageHeader } from '@/components/layout/PageHeader'
import { ExportButtons } from '@/components/shared/ExportButtons'
import { Skeleton } from '@/components/ui/skeleton'
import { useAccountingStore } from '@/hooks/useAccountingData'
import { accountingApi } from '@/lib/accountingApi'
import { downloadCsv, downloadJson, exportStamp } from '@/lib/download'

/**
 * Run an export and say so if it fails.
 *
 * Both exports walk the server page by page now, so either can fail on the
 * network — and `ExportButtons` is deliberately dumb about pending and error
 * state, which used to be harmless when the handler serialized an array that
 * was already resident. An unhandled rejection there is the worst possible
 * answer: no file, no message, no sign the click did anything.
 *
 * @param what - What the user asked for, named in the failure message.
 * @param run - The export itself.
 * @returns A handler for `ExportButtons`.
 */
function exporting(what: string, run: () => Promise<void>): () => Promise<void> {
  return async () => {
    try {
      await run()
    } catch (error) {
      toast.error(`Could not export ${what}: ${error instanceof Error ? error.message : 'the request failed'}`)
    }
  }
}

// Was the "Transactions" tab inside the old combined Accounting page,
// promoted to its own top-level page. The transfer-suggestions panel that
// used to live above this table moved to Rules -> Suggestions instead —
// matching transfers are a rules-authoring task, not a transactions-review one.
export function TransactionsPage() {
  const { data: store, isLoading } = useAccountingStore()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Transactions"
        actions={
          <>
            {/* Both exports walk their collection page by page, and both do
                their own request. "Postings" is the resolved ledger — the
                same rows the table shows, but all of them: an export's
                caller wants everything by definition, which is exactly why
                it cannot read the table's one page. "Ledger" is the raw,
                unresolved history (before rules, overrides, splits, or
                merges). Both also live on Settings' own Export tab, for
                anyone who'd rather find every export in one place. */}
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Postings
              <ExportButtons
                onJson={exporting('postings', async () =>
                  downloadJson(await accountingApi.postingsExport(), `postings-${exportStamp()}.json`),
                )}
                onCsv={exporting('postings', async () =>
                  downloadCsv(await accountingApi.postingsExport(), `postings-${exportStamp()}.csv`),
                )}
              />
            </div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Ledger
              <ExportButtons
                onJson={exporting('the ledger', async () =>
                  downloadJson(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.json`),
                )}
                onCsv={exporting('the ledger', async () =>
                  downloadCsv(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.csv`),
                )}
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
              accounts={store.accounts}
              categories={store.categories}
              tags={store.tags}
              rules={store.transfer_rules}
              transferLinks={store.transfer_links}
            />
          </>
        )}
      </div>
    </div>
  )
}
