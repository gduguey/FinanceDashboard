import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { DashboardTab } from '@/components/accounting/DashboardTab'
import { TransactionsTab } from '@/components/accounting/TransactionsTab'
import { CategoriesTab } from '@/components/accounting/CategoriesTab'
import { TagsTab } from '@/components/accounting/TagsTab'
import { RulesTab } from '@/components/accounting/RulesTab'
import { formatDate, formatCurrency, signColor } from '@/lib/format'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useAccountingStore, usePostings, useTransferSuggestions } from '@/hooks/useAccountingData'

function TransferSuggestionsPanel() {
  const { data } = useTransferSuggestions()
  if (!data?.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Possible transfers not yet caught by a rule</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Other account</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="text-right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((suggestion) => (
              <TableRow key={`${suggestion.posting_id}-${suggestion.other_posting_id}`}>
                <TableCell>{suggestion.account_id}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(suggestion.posted_at.slice(0, 10))}</TableCell>
                <TableCell>{suggestion.other_account_id}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(suggestion.other_posted_at.slice(0, 10))}</TableCell>
                <TableCell className={`text-right tabular-nums ${signColor(suggestion.amount)}`}>
                  {formatCurrency(suggestion.amount, 'USD')}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="mt-2 text-xs text-muted-foreground">
          Each pair looks like one transfer a rule hasn't resolved yet — add a rule, or categorize each side manually.
        </p>
      </CardContent>
    </Card>
  )
}

export function AccountingPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Accounting</h1>
        <DisplayCurrencyToggle />
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
            </TabsList>

            <TabsContent value="dashboard">
              <DashboardTab postings={postings ?? []} accounts={store.accounts} tags={store.tags} displayCurrency={displayCurrency} />
            </TabsContent>

            <TabsContent value="transactions" className="space-y-6">
              <TransferSuggestionsPanel />
              <TransactionsTab postings={postings ?? []} accounts={store.accounts} categories={store.categories} tags={store.tags} />
            </TabsContent>

            <TabsContent value="categories">
              <CategoriesTab categories={store.categories} />
            </TabsContent>

            <TabsContent value="tags">
              <TagsTab tags={store.tags} />
            </TabsContent>

            <TabsContent value="rules">
              <RulesTab rules={store.rules} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
