import { TransactionsTable } from '@/components/accounting/transactions/TransactionsTable'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { usePostingSummary } from '@/hooks/useAccountingData'
import type { Account, Category, Tag, TransferLink, TransferRule } from '@/types/accounting'

export function TransactionsTab({
  accounts,
  categories,
  tags,
  rules,
  transferLinks,
}: {
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
  transferLinks: TransferLink[]
}) {
  // A fact about the whole ledger, not about either tab: both tabs keep their
  // own filter bar, so reading this off one of their pages would make the
  // badge move whenever a filter narrowed the other tab. `GET /postings?limit=1`
  // answers it, and it is the same request the sidebar's onboarding check
  // already makes on every route — so the badge costs nothing.
  const { data: summary } = usePostingSummary()
  const needsCategorizingCount = summary?.counts.needs_categorizing ?? 0

  return (
    <Tabs defaultValue="all">
      <TabsList>
        <TabsTrigger value="all">All transactions</TabsTrigger>
        <TabsTrigger value="uncategorized">Needs categorizing ({needsCategorizingCount.toLocaleString()})</TabsTrigger>
      </TabsList>
      <TabsContent value="all">
        <TransactionsTable
          storageKey="accounting.transactions-filter.all"
          accounts={accounts}
          categories={categories}
          tags={tags}
          rules={rules}
          transferLinks={transferLinks}
          onlyUncategorized={false}
        />
      </TabsContent>
      <TabsContent value="uncategorized">
        <TransactionsTable
          storageKey="accounting.transactions-filter.uncategorized"
          accounts={accounts}
          categories={categories}
          tags={tags}
          rules={rules}
          transferLinks={transferLinks}
          onlyUncategorized
        />
      </TabsContent>
    </Tabs>
  )
}
