import { useMemo } from 'react'
import { RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { TagsCell } from '@/components/accounting/TagsCell'
import { formatCurrency, formatDate } from '@/lib/format'
import { useSortableRows } from '@/hooks/useSortableRows'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSetPostingOverride } from '@/hooks/useAccountingData'
import type { Account, Category, Posting, Tag } from '@/types/accounting'

const ALL = '__all__'
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

interface FilterState {
  search: string
  accountFilter: string
  categoryFilter: string
  tagFilter: string
  startDate: string
  endDate: string
}

function defaultFilterState(): FilterState {
  return { search: '', accountFilter: ALL, categoryFilter: ALL, tagFilter: ALL, startDate: '', endDate: '' }
}

function TransactionsTable({
  storageKey,
  postings,
  accounts,
  categories,
  tags,
  onlyUncategorized,
}: {
  storageKey: string
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  onlyUncategorized: boolean
}) {
  const [filters, setFilters] = usePersistedState<FilterState>(storageKey, defaultFilterState())
  const setOverride = useSetPostingOverride()

  const realAccounts = Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    .sort((a, b) => a.name.localeCompare(b.name))
  const topLevelCategories = Object.values(categories)
    .filter((category) => category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))
  const tagOptions = Object.values(tags).sort((a, b) => a.name.localeCompare(b.name))

  const accountItems = { [ALL]: 'All accounts', ...Object.fromEntries(realAccounts.map((a) => [a.account_id, a.name])) }
  const categoryItems = {
    [ALL]: 'All categories',
    ...Object.fromEntries(topLevelCategories.map((c) => [c.category_id, c.name])),
  }
  const tagItems = { [ALL]: 'All tags', ...Object.fromEntries(tagOptions.map((t) => [t.tag_id, t.name])) }

  const filtered = useMemo(() => {
    return postings
      .filter((posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id))
      .filter((posting) => !onlyUncategorized || posting.category_id === null)
      .filter((posting) => posting.description.toLowerCase().includes(filters.search.toLowerCase()))
      .filter((posting) => filters.accountFilter === ALL || posting.account_id === filters.accountFilter)
      .filter((posting) => filters.categoryFilter === ALL || posting.category_id === filters.categoryFilter)
      .filter((posting) => filters.tagFilter === ALL || posting.tag_ids.includes(filters.tagFilter))
      .filter((posting) => !filters.startDate || posting.posted_at.slice(0, 10) >= filters.startDate)
      .filter((posting) => !filters.endDate || posting.posted_at.slice(0, 10) <= filters.endDate)
  }, [postings, filters, onlyUncategorized])

  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'posted_at')

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <CardTitle>{onlyUncategorized ? 'Needs categorizing' : 'All transactions'}</CardTitle>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            className="w-48"
            placeholder="Search description…"
            value={filters.search}
            onChange={(event) => setFilters({ ...filters, search: event.target.value })}
          />
          <Select value={filters.accountFilter} onValueChange={(value) => value && setFilters({ ...filters, accountFilter: value })}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue items={accountItems} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All accounts</SelectItem>
              {realAccounts.map((account) => (
                <SelectItem key={account.account_id} value={account.account_id}>
                  {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={filters.categoryFilter} onValueChange={(value) => value && setFilters({ ...filters, categoryFilter: value })}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue items={categoryItems} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All categories</SelectItem>
              {topLevelCategories.map((category) => (
                <SelectItem key={category.category_id} value={category.category_id}>
                  {category.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={filters.tagFilter} onValueChange={(value) => value && setFilters({ ...filters, tagFilter: value })}>
            <SelectTrigger size="sm" className="w-36">
              <SelectValue items={tagItems} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All tags</SelectItem>
              {tagOptions.map((tag) => (
                <SelectItem key={tag.tag_id} value={tag.tag_id}>
                  {tag.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            type="date"
            className="w-36"
            value={filters.startDate}
            onChange={(event) => setFilters({ ...filters, startDate: event.target.value })}
          />
          <Input
            type="date"
            className="w-36"
            value={filters.endDate}
            onChange={(event) => setFilters({ ...filters, endDate: event.target.value })}
          />
          <Button variant="ghost" size="sm" onClick={() => setFilters(defaultFilterState())}>
            <RotateCcw className="size-3.5" />
            Reset filters
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {onlyUncategorized ? 'Nothing left to categorize.' : 'No transactions match — import a statement to start.'}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'posted_at'} desc={sort.desc} onClick={() => toggleSort('posted_at')}>
                  Date
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
                  Account
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'description'} desc={sort.desc} onClick={() => toggleSort('description')}>
                  Description
                </SortableTableHead>
                <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
                  Amount
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'category_id'} desc={sort.desc} onClick={() => toggleSort('category_id')}>
                  Category
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'subcategory_id'} desc={sort.desc} onClick={() => toggleSort('subcategory_id')}>
                  Subcategory
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'tag_ids'} desc={sort.desc} onClick={() => toggleSort('tag_ids')}>
                  Tags
                </SortableTableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((posting) => (
                <TableRow key={posting.posting_id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDate(posting.posted_at.slice(0, 10))}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {accounts[posting.account_id]?.name ?? posting.account_id}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">{posting.description}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(posting.amount, posting.currency)}
                  </TableCell>
                  <TableCell>
                    <CategorySelect
                      categories={categories}
                      classification={posting.amount >= 0 ? 'income' : 'expense'}
                      value={posting.category_id}
                      onChange={(categoryId) =>
                        setOverride.mutate({
                          postingId: posting.posting_id,
                          override: { category_id: categoryId, subcategory_id: null },
                        })
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <SubcategorySelect
                      categories={categories}
                      categoryId={posting.category_id}
                      value={posting.subcategory_id}
                      onChange={(subcategoryId) =>
                        setOverride.mutate({ postingId: posting.posting_id, override: { subcategory_id: subcategoryId } })
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <TagsCell
                      tagIds={posting.tag_ids}
                      tags={tags}
                      onChange={(tagIds) => setOverride.mutate({ postingId: posting.posting_id, override: { tag_ids: tagIds } })}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export function TransactionsTab({
  postings,
  accounts,
  categories,
  tags,
}: {
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
}) {
  const needsCategorizingCount = postings.filter(
    (posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id) && posting.category_id === null,
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
          onlyUncategorized
        />
      </TabsContent>
    </Tabs>
  )
}
