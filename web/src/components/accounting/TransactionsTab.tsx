import { memo, useCallback, useEffect, useMemo, useRef, useState, type Ref } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { RotateCcw, Scissors, Sparkles, Undo2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { PostingSplitDialog } from '@/components/accounting/PostingSplitDialog'
import { TagsCell } from '@/components/accounting/TagsCell'
import { formatCurrency, formatDate } from '@/lib/format'
import { realIncomeExpensePostingIds } from '@/lib/postingClassification'
import { useSortableRows } from '@/hooks/useSortableRows'
import { usePersistedState } from '@/hooks/usePersistedState'
import {
  useAiSuggestCategory,
  useDeletePostingSplit,
  usePatternSuggestCategory,
  usePatternSuggestCategoryBulk,
  useSetPostingOverride,
  useValidatePending,
} from '@/hooks/useAccountingData'
import type { Account, Category, ManualOverride, Posting, Tag, TransferRule } from '@/types/accounting'

// Approximate row height (px) the virtualizer reserves before measuring the
// real one — a table row with `p-2 text-sm` cells lands around here.
const ESTIMATED_ROW_HEIGHT = 45
const TABLE_COLUMN_COUNT = 9

const ALL = '__all__'
const UNCATEGORIZED = '__uncategorized__'
const NO_SUBCATEGORY = '__no_subcategory__'
const CONFIRMED = '__confirmed__'
const SPLIT_LEG_PATTERN = /^(.+):split:\d+$/
const PENDING_ITEMS: Record<string, string> = { [ALL]: 'All', ai: 'AI pending', pattern: 'Pattern pending', [CONFIRMED]: 'Confirmed' }

// A split leg's own id encodes the original posting it came from — used to
// offer "undo split" on a leg row instead of "split" (splitting a leg
// further isn't supported; undo and re-split from scratch instead).
function splitOriginalId(postingId: string): string | null {
  return SPLIT_LEG_PATTERN.exec(postingId)?.[1] ?? null
}

function categoriesWithSubcategories(categories: Record<string, Category>): Set<string> {
  const withSubcategories = new Set<string>()
  for (const category of Object.values(categories)) {
    if (category.parent_category_id !== null) withSubcategories.add(category.parent_category_id)
  }
  return withSubcategories
}

// A category with subcategories isn't "categorized" until one of them is
// picked too — otherwise a row would leave "Needs categorizing" the
// instant a category is chosen, before there's ever a chance to also pick
// a subcategory for it.
//
// A posting that isn't a real income/expense leg (i.e. an internal
// transfer between two of your own accounts) is never categorizable at
// all, so it never needs categorizing. And a posting still carrying an
// unconfirmed AI/pattern suggestion (`pending_source !== null`) stays in
// "Needs categorizing" even though it already has a category/subcategory
// filled in — it isn't truly categorized until the suggestion is validated.
function needsCategorizing(posting: Posting, withSubcategories: Set<string>, isRealIncomeExpense: boolean): boolean {
  if (!isRealIncomeExpense) return false
  if (posting.pending_source !== null) return true
  if (posting.category_id === null) return true
  return withSubcategories.has(posting.category_id) && posting.subcategory_id === null
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
  pendingFilter: string
  pendingExclude: boolean
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
    pendingFilter: ALL,
    pendingExclude: false,
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

// Row background for a not-yet-confirmed suggestion — green for an AI
// pick, blue for a category-pattern match, per the user's explicit request
// that the two never share a color (they're also two distinct stored
// `pending_source` values, never one shared flag).
const PENDING_ROW_CLASS: Record<'ai' | 'pattern', string> = {
  ai: 'bg-emerald-50 hover:bg-emerald-100/80 dark:bg-emerald-950/40 dark:hover:bg-emerald-950/60',
  pattern: 'bg-blue-50 hover:bg-blue-100/80 dark:bg-blue-950/40 dark:hover:bg-blue-950/60',
}

interface TransactionRowProps {
  ref?: Ref<HTMLTableRowElement>
  'data-index': number
  posting: Posting
  accountName: string
  resolvedByRuleLabel: string | null
  isRealIncomeExpense: boolean
  categories: Record<string, Category>
  tags: Record<string, Tag>
  withSubcategories: Set<string>
  aiMessage: string | undefined
  aiPending: boolean
  onOverride: (postingId: string, override: ManualOverride) => void
  onAiSuggest: (posting: Posting) => void
  onSplit: (posting: Posting) => void
  onUndoSplit: (originalPostingId: string) => void
  onToggleSelected: (postingId: string, selected: boolean) => void
}

// Extracted and memoized so that state changes scoped to one row (an AI
// suggestion in flight, a split dialog opening) don't force every other
// row in a table that can hold thousands of postings to re-render too —
// only the props of a given row need to have changed. `ref`/`data-index`
// are wired straight to the underlying `<tr>` for the row virtualizer's
// dynamic size measurement (see its `measureElement` usage below).
const TransactionRow = memo(function TransactionRow({
  ref,
  'data-index': dataIndex,
  posting,
  accountName,
  resolvedByRuleLabel,
  isRealIncomeExpense,
  categories,
  tags,
  withSubcategories,
  aiMessage,
  aiPending,
  onOverride,
  onAiSuggest,
  onSplit,
  onUndoSplit,
  onToggleSelected,
}: TransactionRowProps) {
  const originalId = splitOriginalId(posting.posting_id)
  const pendingClass = posting.pending_source ? PENDING_ROW_CLASS[posting.pending_source] : undefined
  return (
    <TableRow ref={ref} data-index={dataIndex} className={pendingClass}>
      <TableCell>
        {posting.pending_source && (
          <input
            type="checkbox"
            className="size-3.5 accent-current"
            checked={posting.pending_selected}
            onChange={(event) => onToggleSelected(posting.posting_id, event.target.checked)}
            aria-label="Keep this suggestion"
          />
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(posting.posted_at.slice(0, 10))}</TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">
        {accountName}
        {resolvedByRuleLabel && (
          <span
            className="ml-1 rounded-sm bg-muted px-1 py-0.5 text-[10px] text-muted-foreground"
            title={`Resolved by rule: ${resolvedByRuleLabel} — deleting that rule reverts this posting`}
          >
            via rule
          </span>
        )}
      </TableCell>
      <TableCell className="max-w-xs truncate">{posting.description}</TableCell>
      <TableCell className="text-right tabular-nums">{formatCurrency(posting.amount, posting.currency)}</TableCell>
      <TableCell>
        {isRealIncomeExpense ? (
          <CategorySelect
            categories={categories}
            classification={posting.amount >= 0 ? 'income' : 'expense'}
            value={posting.category_id}
            onChange={(categoryId) =>
              // Changing category always clears subcategory — it's a child
              // of the OLD category, never carried over. A category with
              // subcategories stays in "Needs categorizing" until one is
              // picked too (see `needsCategorizing`), so this row
              // deliberately doesn't disappear yet.
              onOverride(posting.posting_id, { category_id: categoryId, subcategory_id: null })
            }
          />
        ) : (
          <span className="text-xs text-muted-foreground" title="A transfer between two of your own accounts is never categorized">
            Transfer
          </span>
        )}
      </TableCell>
      <TableCell>
        {isRealIncomeExpense ? (
          <SubcategorySelect
            categories={categories}
            categoryId={posting.category_id}
            value={posting.subcategory_id}
            onChange={(subcategoryId) => onOverride(posting.posting_id, { subcategory_id: subcategoryId })}
          />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <TagsCell tagIds={posting.tag_ids} tags={tags} onChange={(tagIds) => onOverride(posting.posting_id, { tag_ids: tagIds })} />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-0.5">
          {needsCategorizing(posting, withSubcategories, isRealIncomeExpense) && (
            <Button
              variant="ghost"
              size="icon"
              title={aiMessage || 'AI suggestion'}
              disabled={aiPending}
              onClick={() => onAiSuggest(posting)}
            >
              <Sparkles className="size-3.5 text-muted-foreground" />
            </Button>
          )}
          {originalId ? (
            <Button variant="ghost" size="icon" title="Undo split" onClick={() => onUndoSplit(originalId)}>
              <Undo2 className="size-3.5 text-muted-foreground" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" title="Split transaction" onClick={() => onSplit(posting)}>
              <Scissors className="size-3.5 text-muted-foreground" />
            </Button>
          )}
        </div>
        {aiMessage && <p className="max-w-32 text-[10px] text-muted-foreground">{aiMessage}</p>}
      </TableCell>
    </TableRow>
  )
})

function TransactionsTable({
  storageKey,
  postings,
  accounts,
  categories,
  tags,
  rules,
  onlyUncategorized,
}: {
  storageKey: string
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
  onlyUncategorized: boolean
}) {
  const [filters, setFilters] = usePersistedState<FilterState>(storageKey, defaultFilterState())
  const [splitting, setSplitting] = useState<Posting | null>(null)
  const [suggestMessages, setSuggestMessages] = useState<Record<string, string>>({})
  const [bulkSuggesting, setBulkSuggesting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkPatternSuggesting, setBulkPatternSuggesting] = useState(false)
  const setOverride = useSetPostingOverride()
  const deleteSplit = useDeletePostingSplit()
  const aiSuggest = useAiSuggestCategory()
  const patternSuggest = usePatternSuggestCategory()
  const patternSuggestBulk = usePatternSuggestCategoryBulk()
  const validatePending = useValidatePending()

  const runAiSuggest = useCallback(
    async (posting: Posting) => {
      const postingId = posting.posting_id
      setSuggestMessages((prev) => ({ ...prev, [postingId]: 'Asking the AI…' }))
      try {
        const result = await aiSuggest.mutateAsync({ postingId, lockCategoryId: posting.category_id })
        setSuggestMessages((prev) => {
          if (!result.applied) return { ...prev, [postingId]: 'No confident suggestion' }
          const { [postingId]: _removed, ...rest } = prev
          return rest
        })
      } catch (error) {
        setSuggestMessages((prev) => ({
          ...prev,
          [postingId]: error instanceof Error ? error.message : 'AI suggestion failed',
        }))
      }
    },
    [aiSuggest],
  )

  // Only ever run one at a time — the LLM call has real latency, and this
  // avoids hammering the provider with the whole filtered view at once.
  async function runBulkAiSuggest(targets: Posting[]) {
    setBulkSuggesting(true)
    setBulkProgress({ done: 0, total: targets.length })
    for (const [index, posting] of targets.entries()) {
      await runAiSuggest(posting)
      setBulkProgress({ done: index + 1, total: targets.length })
    }
    setBulkSuggesting(false)
    setBulkProgress(null)
  }

  // The backend matches every target posting in a single vectorized pass
  // (see `ledger.patterns.match_patterns_bulk`) — one request regardless
  // of how many postings are targeted, instead of one request per posting.
  async function runBulkPatternSuggest(targets: Posting[]) {
    setBulkPatternSuggesting(true)
    try {
      await patternSuggestBulk.mutateAsync(targets.map((posting) => posting.posting_id))
    } finally {
      setBulkPatternSuggesting(false)
    }
  }

  const handleOverride = useCallback(
    (postingId: string, override: ManualOverride) => setOverride.mutate({ postingId, override }),
    [setOverride],
  )
  const handleUndoSplit = useCallback((originalPostingId: string) => deleteSplit.mutate(originalPostingId), [deleteSplit])
  const handleToggleSelected = useCallback(
    (postingId: string, selected: boolean) => setOverride.mutate({ postingId, override: { pending_selected: selected } }),
    [setOverride],
  )

  const realAccounts = useMemo(
    () =>
      Object.values(accounts)
        .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [accounts],
  )
  const topLevelCategories = useMemo(
    () =>
      Object.values(categories)
        .filter((category) => category.parent_category_id === null)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [categories],
  )
  const subcategories = useMemo(
    () =>
      Object.values(categories)
        .filter((category) => category.parent_category_id !== null)
        .map((category) => ({ ...category, parentName: categories[category.parent_category_id ?? '']?.name ?? '' }))
        .sort((a, b) => a.parentName.localeCompare(b.parentName) || a.name.localeCompare(b.name)),
    [categories],
  )
  const tagOptions = useMemo(() => Object.values(tags).sort((a, b) => a.name.localeCompare(b.name)), [tags])

  const accountItems = useMemo(
    () => ({ [ALL]: 'All accounts', ...Object.fromEntries(realAccounts.map((a) => [a.account_id, a.name])) }),
    [realAccounts],
  )
  const categoryItems = useMemo(
    () => ({
      [ALL]: 'All categories',
      [UNCATEGORIZED]: 'Uncategorized',
      ...Object.fromEntries(topLevelCategories.map((c) => [c.category_id, c.name])),
    }),
    [topLevelCategories],
  )
  const subcategoryItems = useMemo(
    () => ({
      [ALL]: 'All subcategories',
      [NO_SUBCATEGORY]: 'None',
      ...Object.fromEntries(subcategories.map((c) => [c.category_id, `${c.parentName} › ${c.name}`])),
    }),
    [subcategories],
  )
  const tagItems = useMemo(
    () => ({ [ALL]: 'All tags', ...Object.fromEntries(tagOptions.map((t) => [t.tag_id, t.name])) }),
    [tagOptions],
  )

  const withSubcategories = useMemo(() => categoriesWithSubcategories(categories), [categories])
  const ruleLabelById = useMemo(
    () => new Map(rules.map((rule) => [rule.rule_id, rule.description || rule.description_contains])),
    [rules],
  )
  // Computed from the full, unscoped `postings` prop (not `filtered`/`sorted`)
  // — an account filter could otherwise split a transfer pair apart and make
  // sibling-detection wrong. See `postingClassification.ts`.
  const realIds = useMemo(() => realIncomeExpensePostingIds(postings, accounts), [postings, accounts])

  const filtered = useMemo(() => {
    return postings
      .filter((posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id))
      .filter((posting) => !onlyUncategorized || needsCategorizing(posting, withSubcategories, realIds.has(posting.posting_id)))
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
      .filter((posting) => {
        const actual =
          filters.pendingFilter === CONFIRMED ? posting.pending_source === null : posting.pending_source === filters.pendingFilter
        return matchesFilter(actual, filters.pendingFilter, filters.pendingExclude)
      })
  }, [postings, filters, onlyUncategorized, withSubcategories, realIds])

  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'posted_at')
  const bulkTargets = useMemo(
    () => filtered.filter((posting) => needsCategorizing(posting, withSubcategories, realIds.has(posting.posting_id))),
    [filtered, withSubcategories, realIds],
  )
  const pendingInView = useMemo(() => sorted.filter((posting) => posting.pending_source !== null), [sorted])
  const allPendingSelected = pendingInView.length > 0 && pendingInView.every((posting) => posting.pending_selected)
  const somePendingSelected = pendingInView.some((posting) => posting.pending_selected)
  const checkedPendingCount = useMemo(() => pendingInView.filter((posting) => posting.pending_selected).length, [pendingInView])

  function handleValidateSelection() {
    validatePending.mutate(pendingInView.map((posting) => posting.posting_id))
  }

  function handleToggleSelectAllPending() {
    const nextSelected = !allPendingSelected
    for (const posting of pendingInView) {
      if (posting.pending_selected !== nextSelected) handleToggleSelected(posting.posting_id, nextSelected)
    }
  }

  // Only the rows actually scrolled into view get mounted — a table of
  // thousands of postings no longer means thousands of live category
  // dropdowns on screen at once.
  const scrollParentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: sorted.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 12,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom = virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  const selectAllRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = somePendingSelected && !allPendingSelected
  }, [somePendingSelected, allPendingSelected])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{onlyUncategorized ? 'Needs categorizing' : 'All transactions'}</CardTitle>
          {bulkTargets.length > 0 && (
            <Button variant="outline" size="sm" disabled={bulkSuggesting} onClick={() => runBulkAiSuggest(bulkTargets)}>
              <Sparkles className="size-3.5" />
              {bulkProgress ? `Suggesting ${bulkProgress.done}/${bulkProgress.total}…` : `AI suggest all (${bulkTargets.length})`}
            </Button>
          )}
          {bulkTargets.length > 0 && (
            <Button variant="outline" size="sm" disabled={bulkPatternSuggesting} onClick={() => runBulkPatternSuggest(bulkTargets)}>
              <Sparkles className="size-3.5" />
              {bulkPatternSuggesting ? 'Matching patterns…' : `Run pattern suggestions (${bulkTargets.length})`}
            </Button>
          )}
          {pendingInView.length > 0 && (
            <Button variant="outline" size="sm" disabled={validatePending.isPending} onClick={handleValidateSelection}>
              Validate selection ({checkedPendingCount} checked / {pendingInView.length} marked)
            </Button>
          )}
        </div>
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
            width="min-w-40"
            onValueChange={(value) => setFilters({ ...filters, accountFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, accountExclude: exclude })}
          />
          <FilterSelect
            value={filters.categoryFilter}
            exclude={filters.categoryExclude}
            items={categoryItems}
            width="min-w-40"
            onValueChange={(value) => setFilters({ ...filters, categoryFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, categoryExclude: exclude })}
          />
          <FilterSelect
            value={filters.subcategoryFilter}
            exclude={filters.subcategoryExclude}
            items={subcategoryItems}
            width="min-w-40"
            onValueChange={(value) => setFilters({ ...filters, subcategoryFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, subcategoryExclude: exclude })}
          />
          <FilterSelect
            value={filters.tagFilter}
            exclude={filters.tagExclude}
            items={tagItems}
            width="min-w-36"
            onValueChange={(value) => setFilters({ ...filters, tagFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, tagExclude: exclude })}
          />
          <FilterSelect
            value={filters.pendingFilter ?? ALL}
            exclude={filters.pendingExclude ?? false}
            items={PENDING_ITEMS}
            width="min-w-32"
            onValueChange={(value) => setFilters({ ...filters, pendingFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, pendingExclude: exclude })}
          />
          <OptionalDateInput
            value={filters.startDate}
            onChange={(startDate) => setFilters({ ...filters, startDate })}
            placeholder="Any start date"
          />
          <OptionalDateInput
            value={filters.endDate}
            onChange={(endDate) => setFilters({ ...filters, endDate })}
            placeholder="Any end date"
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
          <div ref={scrollParentRef} className="max-h-[70vh] overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    {pendingInView.length > 0 && (
                      <input
                        ref={selectAllRef}
                        type="checkbox"
                        className="size-3.5 accent-current"
                        checked={allPendingSelected}
                        onChange={handleToggleSelectAllPending}
                        aria-label="Select all pending suggestions in view"
                      />
                    )}
                  </TableHead>
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
                {paddingTop > 0 && (
                  <tr>
                    <td colSpan={TABLE_COLUMN_COUNT} style={{ height: paddingTop }} />
                  </tr>
                )}
                {virtualRows.map((virtualRow) => {
                  const posting = sorted[virtualRow.index]
                  return (
                    <TransactionRow
                      key={posting.posting_id}
                      ref={rowVirtualizer.measureElement}
                      data-index={virtualRow.index}
                      posting={posting}
                      accountName={accounts[posting.account_id]?.name ?? posting.account_id}
                      resolvedByRuleLabel={
                        posting.resolved_by_transfer_rule_id ? (ruleLabelById.get(posting.resolved_by_transfer_rule_id) ?? posting.resolved_by_transfer_rule_id) : null
                      }
                      isRealIncomeExpense={realIds.has(posting.posting_id)}
                      categories={categories}
                      tags={tags}
                      withSubcategories={withSubcategories}
                      aiMessage={suggestMessages[posting.posting_id]}
                      aiPending={aiSuggest.isPending || bulkSuggesting || patternSuggest.isPending || bulkPatternSuggesting}
                      onOverride={handleOverride}
                      onAiSuggest={runAiSuggest}
                      onSplit={setSplitting}
                      onUndoSplit={handleUndoSplit}
                      onToggleSelected={handleToggleSelected}
                    />
                  )
                })}
                {paddingBottom > 0 && (
                  <tr>
                    <td colSpan={TABLE_COLUMN_COUNT} style={{ height: paddingBottom }} />
                  </tr>
                )}
              </TableBody>
            </Table>
          </div>
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
  rules,
}: {
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
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
          onlyUncategorized
        />
      </TabsContent>
    </Tabs>
  )
}
