import { useMemo, useState } from 'react'
import { RotateCcw, Scissors, Sparkles, Undo2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { PostingSplitDialog } from '@/components/accounting/PostingSplitDialog'
import { TagsCell } from '@/components/accounting/TagsCell'
import { formatCurrency, formatDate } from '@/lib/format'
import { useSortableRows } from '@/hooks/useSortableRows'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useAiSuggestCategory, useDeletePostingSplit, useSetPostingOverride } from '@/hooks/useAccountingData'
import type { Account, Category, Posting, Tag } from '@/types/accounting'

const ALL = '__all__'
const UNCATEGORIZED = '__uncategorized__'
const NO_SUBCATEGORY = '__no_subcategory__'
const SPLIT_LEG_PATTERN = /^(.+):split:\d+$/

// A split leg's own id encodes the original posting it came from — used to
// offer "undo split" on a leg row instead of "split" (splitting a leg
// further isn't supported; undo and re-split from scratch instead).
function splitOriginalId(postingId: string): string | null {
  return SPLIT_LEG_PATTERN.exec(postingId)?.[1] ?? null
}
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

interface FilterState {
  search: string
  accountFilter: string
  accountExclude: boolean
  categoryFilter: string
  categoryExclude: boolean
  subcategoryFilter: string
  subcategoryExclude: boolean
  tagFilter: string
  tagExclude: boolean
  startDate: string
  endDate: string
}

function defaultFilterState(): FilterState {
  return {
    search: '',
    accountFilter: ALL,
    accountExclude: false,
    categoryFilter: ALL,
    categoryExclude: false,
    subcategoryFilter: ALL,
    subcategoryExclude: false,
    tagFilter: ALL,
    tagExclude: false,
    startDate: '',
    endDate: '',
  }
}

// A filter value paired with an "Is"/"Not" toggle — the "show everything
// but this one" mode the equality filters below share. `undefined`/unset
// values from a filter state persisted before this field existed are
// treated as "no filter", never as "matches nothing".
function matchesFilter(actual: boolean, filterValue: string | undefined, exclude: boolean | undefined): boolean {
  if (!filterValue || filterValue === ALL) return true
  return exclude ? !actual : actual
}

function FilterSelect({
  value,
  exclude,
  items,
  width,
  onValueChange,
  onExcludeChange,
}: {
  value: string
  exclude: boolean
  items: Record<string, string>
  width: string
  onValueChange: (value: string) => void
  onExcludeChange: (exclude: boolean) => void
}) {
  return (
    <div className="flex items-center gap-1">
      <Select value={value} onValueChange={(next) => next && onValueChange(next)}>
        <SelectTrigger size="sm" className={width}>
          <SelectValue items={items} />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([id, name]) => (
            <SelectItem key={id} value={id}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {value !== ALL && (
        <Button
          type="button"
          variant={exclude ? 'default' : 'outline'}
          size="sm"
          className="h-8 px-2 text-xs"
          onClick={() => onExcludeChange(!exclude)}
          title={exclude ? 'Excluding this — click to include instead' : 'Including this — click to exclude instead'}
        >
          {exclude ? 'Not' : 'Is'}
        </Button>
      )}
    </div>
  )
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
  const [splitting, setSplitting] = useState<Posting | null>(null)
  const [aiMessages, setAiMessages] = useState<Record<string, string>>({})
  const setOverride = useSetPostingOverride()
  const deleteSplit = useDeletePostingSplit()
  const aiSuggest = useAiSuggestCategory()

  async function runAiSuggest(postingId: string) {
    setAiMessages((prev) => ({ ...prev, [postingId]: 'Asking the AI…' }))
    try {
      const result = await aiSuggest.mutateAsync(postingId)
      setAiMessages((prev) => {
        if (!result.applied) return { ...prev, [postingId]: 'No confident suggestion' }
        const { [postingId]: _removed, ...rest } = prev
        return rest
      })
    } catch (error) {
      setAiMessages((prev) => ({
        ...prev,
        [postingId]: error instanceof Error ? error.message : 'AI suggestion failed',
      }))
    }
  }

  const realAccounts = Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    .sort((a, b) => a.name.localeCompare(b.name))
  const topLevelCategories = Object.values(categories)
    .filter((category) => category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))
  const subcategories = Object.values(categories)
    .filter((category) => category.parent_category_id !== null)
    .map((category) => ({ ...category, parentName: categories[category.parent_category_id ?? '']?.name ?? '' }))
    .sort((a, b) => a.parentName.localeCompare(b.parentName) || a.name.localeCompare(b.name))
  const tagOptions = Object.values(tags).sort((a, b) => a.name.localeCompare(b.name))

  const accountItems = { [ALL]: 'All accounts', ...Object.fromEntries(realAccounts.map((a) => [a.account_id, a.name])) }
  const categoryItems = {
    [ALL]: 'All categories',
    [UNCATEGORIZED]: 'Uncategorized',
    ...Object.fromEntries(topLevelCategories.map((c) => [c.category_id, c.name])),
  }
  const subcategoryItems = {
    [ALL]: 'All subcategories',
    [NO_SUBCATEGORY]: 'None',
    ...Object.fromEntries(subcategories.map((c) => [c.category_id, `${c.parentName} › ${c.name}`])),
  }
  const tagItems = { [ALL]: 'All tags', ...Object.fromEntries(tagOptions.map((t) => [t.tag_id, t.name])) }

  const filtered = useMemo(() => {
    return postings
      .filter((posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id))
      .filter((posting) => !onlyUncategorized || posting.category_id === null)
      .filter((posting) => posting.description.toLowerCase().includes(filters.search.toLowerCase()))
      .filter((posting) => matchesFilter(posting.account_id === filters.accountFilter, filters.accountFilter, filters.accountExclude))
      .filter((posting) => {
        const actual =
          filters.categoryFilter === UNCATEGORIZED ? posting.category_id === null : posting.category_id === filters.categoryFilter
        return matchesFilter(actual, filters.categoryFilter, filters.categoryExclude)
      })
      .filter((posting) => {
        const actual =
          filters.subcategoryFilter === NO_SUBCATEGORY
            ? posting.subcategory_id === null
            : posting.subcategory_id === filters.subcategoryFilter
        return matchesFilter(actual, filters.subcategoryFilter, filters.subcategoryExclude)
      })
      .filter((posting) => matchesFilter(posting.tag_ids.includes(filters.tagFilter), filters.tagFilter, filters.tagExclude))
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
          <FilterSelect
            value={filters.accountFilter}
            exclude={filters.accountExclude}
            items={accountItems}
            width="w-40"
            onValueChange={(value) => setFilters({ ...filters, accountFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, accountExclude: exclude })}
          />
          <FilterSelect
            value={filters.categoryFilter}
            exclude={filters.categoryExclude}
            items={categoryItems}
            width="w-40"
            onValueChange={(value) => setFilters({ ...filters, categoryFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, categoryExclude: exclude })}
          />
          <FilterSelect
            value={filters.subcategoryFilter}
            exclude={filters.subcategoryExclude}
            items={subcategoryItems}
            width="w-40"
            onValueChange={(value) => setFilters({ ...filters, subcategoryFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, subcategoryExclude: exclude })}
          />
          <FilterSelect
            value={filters.tagFilter}
            exclude={filters.tagExclude}
            items={tagItems}
            width="w-36"
            onValueChange={(value) => setFilters({ ...filters, tagFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, tagExclude: exclude })}
          />
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
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((posting) => {
                const originalId = splitOriginalId(posting.posting_id)
                return (
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
                      onChange={(categoryId) => {
                        // A category with any subcategories always has an
                        // "Other" catch-all (backend-enforced) — defaulting
                        // to it here means picking a category alone already
                        // fully categorizes the row, so it doesn't need a
                        // second, separate subcategory step before leaving
                        // "Needs categorizing".
                        const otherId = categoryId ? `${categoryId}:other` : null
                        const subcategoryId = otherId && categories[otherId] ? otherId : null
                        setOverride.mutate({
                          postingId: posting.posting_id,
                          override: { category_id: categoryId, subcategory_id: subcategoryId },
                        })
                      }}
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
                  <TableCell>
                    <div className="flex items-center gap-0.5">
                      {posting.category_id === null && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title={aiMessages[posting.posting_id] || 'AI suggestion'}
                          disabled={aiSuggest.isPending}
                          onClick={() => runAiSuggest(posting.posting_id)}
                        >
                          <Sparkles className="size-3.5 text-muted-foreground" />
                        </Button>
                      )}
                      {originalId ? (
                        <Button variant="ghost" size="icon" title="Undo split" onClick={() => deleteSplit.mutate(originalId)}>
                          <Undo2 className="size-3.5 text-muted-foreground" />
                        </Button>
                      ) : (
                        <Button variant="ghost" size="icon" title="Split transaction" onClick={() => setSplitting(posting)}>
                          <Scissors className="size-3.5 text-muted-foreground" />
                        </Button>
                      )}
                    </div>
                    {aiMessages[posting.posting_id] && (
                      <p className="max-w-32 text-[10px] text-muted-foreground">{aiMessages[posting.posting_id]}</p>
                    )}
                  </TableCell>
                </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {splitting && <PostingSplitDialog posting={splitting} categories={categories} onClose={() => setSplitting(null)} />}
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
