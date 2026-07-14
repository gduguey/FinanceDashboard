import { useVirtualizer } from '@tanstack/react-virtual'
import { Archive, ChevronLeft, ChevronRight, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { SuggestionArchive } from '@/components/accounting/SuggestionArchive'
import { FilterSelect } from '@/components/shared/FilterSelect'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCreatePostingMerge, useDismissSuggestion, useDuplicateSuggestions } from '@/hooks/useAccountingData'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSortableRows } from '@/hooks/useSortableRows'
import { FILTER_ALL, matchesFilter } from '@/lib/filters'
import { formatCurrency, formatDate } from '@/lib/format'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import type { Account, DuplicateGroup, PostingMergeUpsert } from '@/types/accounting'

const ESTIMATED_ROW_HEIGHT = 44
const CHECKED = '__checked__'
const UNCHECKED = '__unchecked__'
const CHECKED_ITEMS: Record<string, string> = { [FILTER_ALL]: 'All', [CHECKED]: 'Checked', [UNCHECKED]: 'Unchecked' }

interface DuplicateGroupRow extends DuplicateGroup {
  earliestDate: string
  amount: number
  descriptionsPreview: string
  postingsCount: number
  // `useSortableRows` always starts a fresh sort key in descending order —
  // sorting on this inverse of `certainty` instead makes "least certain
  // first" (the ones needing the closest review) the default view, while
  // still toggling to "most certain first" on a second click.
  urgency: number
}

interface FilterState {
  accountFilter: string
  accountExclude: boolean
  dateFilter: string
  dateExclude: boolean
  checkedFilter: string
  checkedExclude: boolean
}

function defaultFilterState(): FilterState {
  return {
    accountFilter: FILTER_ALL,
    accountExclude: false,
    dateFilter: '',
    dateExclude: false,
    checkedFilter: FILTER_ALL,
    checkedExclude: false,
  }
}

function toRow(group: DuplicateGroup): DuplicateGroupRow {
  // `posted_at` is a full ISO datetime, not a bare date — `formatDate`
  // expects `YYYY-MM-DD` and silently produces "Invalid Date" if handed
  // the time portion too, so it's stripped here before anything reads it.
  const dates = group.postings.map((posting) => posting.posted_at.slice(0, 10))
  return {
    ...group,
    earliestDate: dates.reduce((min, date) => (date < min ? date : min), dates[0]),
    amount: group.postings[0].amount,
    descriptionsPreview: [...new Set(group.postings.map((posting) => posting.description))].join(' / '),
    postingsCount: group.postings.length,
    urgency: 1 - group.certainty,
  }
}

// The longest description usually carries the most detail (a bank's
// terse recurring feed vs. a richer one-off memo for the same purchase),
// so it's the sensible unattended default — used both to pre-select a
// radio in the review dialog and to decide a bulk accept's kept leg.
function pickDefaultKeptPosting(group: DuplicateGroupRow) {
  const sortedPostings = [...group.postings].sort((a, b) => a.posted_at.localeCompare(b.posted_at))
  return sortedPostings.reduce(
    (longest, posting) => (posting.description.length > longest.description.length ? posting : longest),
    sortedPostings[0],
  )
}

function MergeReviewDialog({
  group,
  currency,
  accountName,
  position,
  hasPrevious,
  hasNext,
  onClose,
  onConfirm,
  onPrevious,
  onNext,
  isSubmitting,
}: {
  group: DuplicateGroupRow
  currency: string
  accountName: string
  position: string
  hasPrevious: boolean
  hasNext: boolean
  onClose: () => void
  onConfirm: (merge: PostingMergeUpsert) => void
  onPrevious: () => void
  onNext: () => void
  isSubmitting: boolean
}) {
  const sortedPostings = useMemo(
    () => [...group.postings].sort((a, b) => a.posted_at.localeCompare(b.posted_at)),
    [group.postings],
  )
  const defaultKept = useMemo(() => pickDefaultKeptPosting(group), [group])
  const [keptTransactionId, setKeptTransactionId] = useState(defaultKept.transaction_id)
  const [description, setDescription] = useState(defaultKept.description)
  const keptPosting =
    group.postings.find((posting) => posting.transaction_id === keptTransactionId) ?? group.postings[0]

  // Arrow keys step through suggestions without closing the dialog — held
  // off the input though, so typing in the description field can't hijack
  // the caret with a stray Left/Right.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.target instanceof HTMLInputElement) return
      if (event.key === 'ArrowLeft' && hasPrevious) onPrevious()
      if (event.key === 'ArrowRight' && hasNext) onNext()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [hasPrevious, hasNext, onPrevious, onNext])

  function selectKept(transactionId: string, fallbackDescription: string) {
    setKeptTransactionId(transactionId)
    setDescription(fallbackDescription)
  }

  function handleConfirm() {
    onConfirm({
      kept_transaction_id: keptTransactionId,
      duplicate_transaction_ids: group.postings
        .filter((posting) => posting.transaction_id !== keptTransactionId)
        .map((posting) => posting.transaction_id),
      description: description.trim() || null,
    })
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        {hasPrevious && (
          <button
            type="button"
            onClick={onPrevious}
            title="Previous suggestion (←)"
            className="absolute top-1/2 -left-12 hidden -translate-y-1/2 rounded-full border border-border bg-popover p-2 text-muted-foreground hover:text-foreground sm:flex"
          >
            <ChevronLeft className="size-4" />
          </button>
        )}
        {hasNext && (
          <button
            type="button"
            onClick={onNext}
            title="Next suggestion (→)"
            className="absolute top-1/2 -right-12 hidden -translate-y-1/2 rounded-full border border-border bg-popover p-2 text-muted-foreground hover:text-foreground sm:flex"
          >
            <ChevronRight className="size-4" />
          </button>
        )}
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between gap-2 pr-6">
            Merge duplicate transactions
            <span className="text-xs font-normal text-muted-foreground">{position}</span>
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            These {group.postings.length} transactions on {accountName} look like the same purchase, recorded more than
            once. Pick which one to keep — the others are dropped entirely, both their legs.
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">Keep</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedPostings.map((posting) => (
                <TableRow key={posting.transaction_id}>
                  <TableCell>
                    <input
                      type="radio"
                      name={`keep-${group.group_key}`}
                      checked={posting.transaction_id === keptTransactionId}
                      onChange={() => selectKept(posting.transaction_id, posting.description)}
                      className="size-3.5 accent-current"
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDate(posting.posted_at.slice(0, 10))}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{posting.description}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCurrency(posting.amount, currency)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="space-y-2 rounded-md border border-border p-3">
            <p className="text-xs text-muted-foreground">Resulting transaction:</p>
            <div className="flex items-center gap-3 text-sm">
              <span className="whitespace-nowrap text-muted-foreground">
                {formatDate(keptPosting.posted_at.slice(0, 10))}
              </span>
              <Input value={description} onChange={(event) => setDescription(event.target.value)} className="flex-1" />
              <span className="tabular-nums font-medium">{formatCurrency(keptPosting.amount, currency)}</span>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={isSubmitting}>
            Merge
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Suggests — never applies — likely duplicate transactions: the same real
// purchase imported from two different sources with two different ids.
// Checking a row is just a "reviewed" marker to filter by, independent of
// the actual merge decision, which always goes through the review dialog
// so the kept transaction and its description are chosen deliberately.
export function DuplicateSuggestionsPanel({ accounts }: { accounts: Record<string, Account> }) {
  const [windowDays, setWindowDays] = usePersistedState('accounting.duplicate-suggestions.window-days', 3)
  const [windowDaysDraft, setWindowDaysDraft] = useState(String(windowDays))
  const { data, isLoading, isError, error } = useDuplicateSuggestions(windowDays)
  const [filters, setFilters] = useState<FilterState>(defaultFilterState())
  const [checkedKeys, setCheckedKeys] = useState<Set<string>>(new Set())
  const [reviewingIndex, setReviewingIndex] = useState<number | null>(null)
  const createMerge = useCreatePostingMerge()
  const dismissSuggestion = useDismissSuggestion()

  function dismiss(row: DuplicateGroupRow) {
    dismissSuggestion.mutate({
      suggestion_id: row.suggestion_id,
      kind: 'duplicate',
      description: row.descriptionsPreview,
    })
  }

  function handleWindowDaysChange(value: string) {
    setWindowDaysDraft(value)
    const parsed = Number.parseInt(value, 10)
    if (value !== '' && Number.isFinite(parsed) && parsed >= 1) setWindowDays(parsed)
  }

  function handleWindowDaysBlur() {
    const parsed = Number.parseInt(windowDaysDraft, 10)
    if (windowDaysDraft === '' || !Number.isFinite(parsed) || parsed < 1) setWindowDaysDraft(String(windowDays))
  }

  function toggleChecked(groupKey: string, checked: boolean) {
    setCheckedKeys((prev) => {
      const next = new Set(prev)
      if (checked) next.add(groupKey)
      else next.delete(groupKey)
      return next
    })
  }

  const rows = useMemo(() => (data ?? []).map(toRow), [data])

  const accountItems = useMemo(() => {
    const ids = [...new Set(rows.map((row) => row.account_id))]
    return { [FILTER_ALL]: 'All accounts', ...Object.fromEntries(ids.map((id) => [id, accounts[id]?.name ?? id])) }
  }, [rows, accounts])

  const filtered = rows
    .filter((row) =>
      matchesFilter(row.account_id === filters.accountFilter, filters.accountFilter, filters.accountExclude),
    )
    .filter((row) =>
      matchesFilter(
        row.postings.some((posting) => posting.posted_at.slice(0, 10) === filters.dateFilter),
        filters.dateFilter,
        filters.dateExclude,
      ),
    )
    .filter((row) => {
      const isChecked = checkedKeys.has(row.group_key)
      const actual = filters.checkedFilter === CHECKED ? isChecked : !isChecked
      return matchesFilter(actual, filters.checkedFilter, filters.checkedExclude)
    })

  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'urgency')
  const allChecked = sorted.length > 0 && sorted.every((row) => checkedKeys.has(row.group_key))
  const reviewing = reviewingIndex !== null ? (sorted[reviewingIndex] ?? null) : null
  const checkedRows = rows.filter((row) => checkedKeys.has(row.group_key))
  const checkedPostingsCount = checkedRows.reduce((sum, row) => sum + row.postings.length, 0)

  function toggleSelectAll() {
    setCheckedKeys((prev) => {
      const next = new Set(prev)
      if (allChecked) {
        for (const row of sorted) next.delete(row.group_key)
      } else {
        for (const row of sorted) next.add(row.group_key)
      }
      return next
    })
  }

  const scrollParentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: sorted.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 12,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  function handleConfirmMerge(merge: PostingMergeUpsert) {
    createMerge.mutate(merge, { onSuccess: () => setReviewingIndex(null) })
  }

  // Skips the per-group review dialog entirely — each checked group is
  // merged using the same "longest description wins" default the dialog
  // itself pre-selects, so this is exactly what confirming every checked
  // group one-by-one without changes would have produced. Each group is
  // its own independent POST rather than one batched request — there's no
  // single endpoint left that accepts more than one merge at a time.
  async function handleBulkAccept() {
    await Promise.all(
      checkedRows.map((row) => {
        const kept = pickDefaultKeptPosting(row)
        const merge: PostingMergeUpsert = {
          kept_transaction_id: kept.transaction_id,
          duplicate_transaction_ids: row.postings
            .filter((posting) => posting.transaction_id !== kept.transaction_id)
            .map((posting) => posting.transaction_id),
          description: null,
        }
        return createMerge.mutateAsync(merge)
      }),
    )
    setCheckedKeys(new Set())
  }

  if (isLoading) return null

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Possible duplicate transactions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {error?.message || "Couldn't check for duplicates — try again in a moment."}
          </p>
        </CardContent>
      </Card>
    )
  }

  if (!hasAnyRealAccount(Object.values(accounts))) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Possible duplicate transactions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No accounts yet — import a statement under "Import statements" above to get started.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <div className="flex items-center gap-3">
          <CardTitle>Possible duplicate transactions</CardTitle>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Search within
            <Input
              type="number"
              min={1}
              className="w-16"
              value={windowDaysDraft}
              onChange={(event) => handleWindowDaysChange(event.target.value)}
              onBlur={handleWindowDaysBlur}
            />
            days
          </label>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <FilterSelect
            value={filters.accountFilter}
            exclude={filters.accountExclude}
            items={accountItems}
            width="min-w-40"
            onValueChange={(value) => setFilters({ ...filters, accountFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, accountExclude: exclude })}
          />
          <div className="flex items-center gap-1">
            <OptionalDateInput
              value={filters.dateFilter}
              onChange={(dateFilter) => setFilters({ ...filters, dateFilter })}
              className="w-36"
            />
            {filters.dateFilter && (
              <Button
                type="button"
                variant={filters.dateExclude ? 'default' : 'outline'}
                size="sm"
                className="h-8 px-2 text-xs"
                onClick={() => setFilters({ ...filters, dateExclude: !filters.dateExclude })}
              >
                {filters.dateExclude ? 'Not' : 'Is'}
              </Button>
            )}
          </div>
          <FilterSelect
            value={filters.checkedFilter}
            exclude={filters.checkedExclude}
            items={CHECKED_ITEMS}
            width="min-w-32"
            onValueChange={(value) => setFilters({ ...filters, checkedFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, checkedExclude: exclude })}
          />
          <Button variant="ghost" size="sm" onClick={() => setFilters(defaultFilterState())}>
            <RotateCcw className="size-3.5" />
            Reset filters
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {checkedRows.length > 0 && (
          <div className="mb-3 flex justify-end">
            <Button size="sm" onClick={handleBulkAccept} disabled={createMerge.isPending}>
              Accept merging {checkedPostingsCount} transactions into {checkedRows.length} transactions (
              {checkedRows.length}/{rows.length})
            </Button>
          </div>
        )}
        {sorted.length === 0 ? (
          <p className="text-sm text-muted-foreground">No likely duplicates within {windowDays} days.</p>
        ) : (
          <div ref={scrollParentRef} className="max-h-[70vh] overflow-x-auto overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <input
                      type="checkbox"
                      className="size-3.5 accent-current"
                      checked={allChecked}
                      onChange={toggleSelectAll}
                      aria-label="Select all duplicate groups in view"
                    />
                  </TableHead>
                  <SortableTableHead
                    active={sort.key === 'account_id'}
                    desc={sort.desc}
                    onClick={() => toggleSort('account_id')}
                  >
                    Account
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'earliestDate'}
                    desc={sort.desc}
                    onClick={() => toggleSort('earliestDate')}
                  >
                    Date
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'descriptionsPreview'}
                    desc={sort.desc}
                    onClick={() => toggleSort('descriptionsPreview')}
                  >
                    Description
                  </SortableTableHead>
                  <SortableTableHead
                    align="right"
                    active={sort.key === 'amount'}
                    desc={sort.desc}
                    onClick={() => toggleSort('amount')}
                  >
                    Amount
                  </SortableTableHead>
                  <SortableTableHead
                    align="right"
                    active={sort.key === 'postingsCount'}
                    desc={sort.desc}
                    onClick={() => toggleSort('postingsCount')}
                  >
                    # found
                  </SortableTableHead>
                  <SortableTableHead
                    align="right"
                    active={sort.key === 'urgency'}
                    desc={sort.desc}
                    onClick={() => toggleSort('urgency')}
                  >
                    Certainty
                  </SortableTableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {paddingTop > 0 && (
                  <TableRow>
                    <TableCell colSpan={8} style={{ height: paddingTop, padding: 0 }} />
                  </TableRow>
                )}
                {virtualRows.map((virtualRow) => {
                  const row = sorted[virtualRow.index]
                  const currency = accounts[row.account_id]?.currency ?? 'USD'
                  return (
                    <TableRow
                      key={row.group_key}
                      data-index={virtualRow.index}
                      className="cursor-pointer hover:bg-muted/40"
                      onClick={() => setReviewingIndex(virtualRow.index)}
                    >
                      <TableCell onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="size-3.5 accent-current"
                          checked={checkedKeys.has(row.group_key)}
                          onChange={(event) => toggleChecked(row.group_key, event.target.checked)}
                        />
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {accounts[row.account_id]?.name ?? row.account_id}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDate(row.earliestDate)}
                      </TableCell>
                      <TableCell
                        className="max-w-[220px] truncate text-muted-foreground"
                        title={row.descriptionsPreview}
                      >
                        {row.descriptionsPreview}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatCurrency(row.amount, currency)}</TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {row.postings.length}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {Math.round(row.certainty * 100)}%
                      </TableCell>
                      <TableCell onClick={(event) => event.stopPropagation()}>
                        <Button variant="ghost" size="icon" title="Not a duplicate" onClick={() => dismiss(row)}>
                          <Archive className="size-3.5 text-muted-foreground" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
                {paddingBottom > 0 && (
                  <TableRow>
                    <TableCell colSpan={8} style={{ height: paddingBottom, padding: 0 }} />
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}
        <div className="mt-4">
          <SuggestionArchive kind="duplicate" />
        </div>
      </CardContent>

      {reviewing && reviewingIndex !== null && (
        <MergeReviewDialog
          key={reviewing.group_key}
          group={reviewing}
          currency={accounts[reviewing.account_id]?.currency ?? 'USD'}
          accountName={accounts[reviewing.account_id]?.name ?? reviewing.account_id}
          position={`${reviewingIndex + 1} / ${sorted.length}`}
          hasPrevious={reviewingIndex > 0}
          hasNext={reviewingIndex < sorted.length - 1}
          onClose={() => setReviewingIndex(null)}
          onConfirm={handleConfirmMerge}
          onPrevious={() => setReviewingIndex((index) => (index !== null ? Math.max(0, index - 1) : index))}
          onNext={() => setReviewingIndex((index) => (index !== null ? Math.min(sorted.length - 1, index + 1) : index))}
          isSubmitting={createMerge.isPending}
        />
      )}
    </Card>
  )
}
