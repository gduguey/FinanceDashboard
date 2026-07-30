import { useMemo } from 'react'
import { categoriesWithSubcategories, needsCategorizing } from '@/components/accounting/transactionCategorization'
import { TransactionsTable } from '@/components/accounting/transactions/TransactionsTable'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { realIncomeExpensePostingIds } from '@/lib/postingClassification'
import { PLACEHOLDER_ACCOUNT_IDS } from '@/lib/transactionFilters'
import type { Account, Category, Posting, Tag, TransferLink, TransferRule } from '@/types/accounting'

export function TransactionsTab({
  postings,
  accounts,
  categories,
  tags,
  rules,
  transferLinks,
}: {
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
  transferLinks: TransferLink[]
}) {
  const withSubcategories = useMemo(() => categoriesWithSubcategories(categories), [categories])
  const realIds = useMemo(() => realIncomeExpensePostingIds(postings, accounts), [postings, accounts])
  const needsCategorizingCount = postings.filter(
    (posting) =>
      !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id) &&
      needsCategorizing(posting, withSubcategories, realIds.has(posting.posting_id)),
  ).length

  return (
    <Tabs defaultValue="all">
      <TabsList>
        <TabsTrigger value="all">All transactions</TabsTrigger>
        <TabsTrigger value="uncategorized">Needs categorizing ({needsCategorizingCount})</TabsTrigger>
      </TabsList>
      <TabsContent value="all">
        <TransactionsTable
          storageKey="accounting.transactions-filter.all"
          postings={postings}
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
          postings={postings}
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
