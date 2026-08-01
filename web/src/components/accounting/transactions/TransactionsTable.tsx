import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { PostingSplitDialog } from '@/components/accounting/PostingSplitDialog'
import { categoriesWithSubcategories } from '@/components/accounting/transactionCategorization'
import { TransactionRow } from '@/components/accounting/transactions/TransactionRow'
import { TransactionsFilterBar } from '@/components/accounting/transactions/TransactionsFilterBar'
import { TransactionsTableHead } from '@/components/accounting/transactions/TransactionsTableHead'
import { TransactionsToolbar } from '@/components/accounting/transactions/TransactionsToolbar'
import { TransferDetailDialog } from '@/components/accounting/transactions/TransferDetailDialog'
import { useTransactionActions } from '@/components/accounting/transactions/useTransactionActions'
import { TablePagination } from '@/components/shared/TablePagination'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Table, TableBody } from '@/components/ui/table'
import { usePostingMonths, usePostingsPage } from '@/hooks/useAccountingData'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { usePersistedState } from '@/hooks/usePersistedState'
import { FILTER_ALL as ALL } from '@/lib/filters'
import { formatCurrency, formatMonthLong } from '@/lib/format'
import { pickHintByPostingId } from '@/lib/pickHints'
import {
  ALL_MONTHS,
  defaultFilterState,
  NO_SUBCATEGORY,
  normalizeFilterState,
  type PersistedFilters,
  PLACEHOLDER_ACCOUNT_IDS,
  toPostingFilters,
  UNCATEGORIZED,
  withSearch,
} from '@/lib/transactionFilters'
import { transferBadgeByPostingId as buildTransferBadges } from '@/lib/transferBadges'
import type { Account, Category, Posting, PostingSortField, Tag, TransferLink, TransferRule } from '@/types/accounting'

// Approximate row height (px) the virtualizer reserves before measuring the
// real one — a table row with `p-2 text-sm` cells lands around here.
const ESTIMATED_ROW_HEIGHT = 45
const TABLE_COLUMN_COUNT = 9
// Long enough that a burst of typing settles once, short enough that the table
// still feels like it is tracking the box.
const SEARCH_DEBOUNCE_MS = 200
// Transactions per page, matching `http_api.pagination.PAGE_LIMIT_DEFAULT` and
// the size the latency gate measures. Counted in transactions, not rows: a
// page carries every leg of each, so `items` is normally about twice this.
const PAGE_SIZE = 200

export function TransactionsTable({
  storageKey,
  accounts,
  categories,
  tags,
  rules,
  transferLinks,
  onlyUncategorized,
}: {
  storageKey: string
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
  transferLinks: TransferLink[]
  onlyUncategorized: boolean
}) {
  const [stored, setStoredFilters] = usePersistedState<PersistedFilters>(storageKey, defaultFilterState())
  // Whatever vintage of filter state `localStorage` holds is coerced into a
  // usable one in one place — see `normalizeFilterState` for what a stale
  // single-select value used to do to a `.includes` check.
  const persisted = useMemo(() => normalizeFilterState(stored), [stored])
  // The search term is component state rather than persisted state, and the
  // filter reads it a beat behind the box. Typing then costs a controlled
  // input's own re-render instead of a `JSON.stringify`, a `localStorage`
  // write and a request per keystroke — see `PersistedFilters` and
  // `useDebouncedValue`.
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebouncedValue(search, SEARCH_DEBOUNCE_MS)
  const filters = useMemo(() => withSearch(persisted, debouncedSearch), [persisted, debouncedSearch])
  const [sort, setSort] = useState<{ key: PostingSortField; desc: boolean }>({ key: 'posted_at', desc: true })
  const [offset, setOffset] = useState(0)

  // The whole filter bar as the server reads it — the single translation
  // between the screen's vocabulary and the wire's. Nothing below re-derives
  // a predicate from it.
  const query = useMemo(() => toPostingFilters(filters, { onlyUncategorized }), [filters, onlyUncategorized])
  const page = usePostingsPage({ ...query, sort: sort.key, descending: sort.desc, limit: PAGE_SIZE, offset })
  const { data: months } = usePostingMonths()

  // Any change to what is being asked for returns to the first page. Without
  // this, a filter narrowing the collection to fewer transactions than
  // `offset` would land on an empty page that reads as "nothing matches".
  const queryFingerprint = JSON.stringify([query, sort])
  const lastFingerprint = useRef(queryFingerprint)
  if (lastFingerprint.current !== queryFingerprint) {
    lastFingerprint.current = queryFingerprint
    if (offset !== 0) setOffset(0)
  }

  const rows = useMemo(() => page.data?.items ?? [], [page.data])
  const counts = page.data?.counts
  const total = page.data?.total ?? 0
  // React Query is holding the previous page while the next is in flight, so
  // what is on screen belongs to a query nobody is asking for any more.
  const stale = page.isPlaceholderData

  const [splitting, setSplitting] = useState<Posting | null>(null)
  const {
    suggestMessages,
    bulkProgress,
    bulkSuggesting,
    bulkPatternSuggesting,
    aiAvailable,
    aiPending,
    validatePendingIsPending,
    safeCounterpartyAccounts,
    pickingSource,
    runAiSuggest,
    runBulkAiSuggest,
    runBulkPatternSuggest,
    validateFiltered,
    handleOverride,
    handleMarkAsTransfer,
    handleUndoManualOverride,
    handleExcludeFromRule,
    handleExcludeAndUnlinkFromRule,
    handleUnlinkTransfer,
    handleStartPicking,
    handleCancelPicking,
    handlePickTarget,
    handleUndoSplit,
    handleToggleSelected,
  } = useTransactionActions(rows, accounts, rules)

  // Read off each row rather than derived by scanning the ledger: the server
  // computes `is_real_income_expense`, mirroring
  // `dashboard.income_statement.real_income_expense_legs`, and stores it.
  const realIds = useMemo(
    () => new Set(rows.filter((posting) => posting.is_real_income_expense).map((posting) => posting.posting_id)),
    [rows],
  )
  // Everything the category-column badge and its detail popup need. The
  // partner of a confirmed transfer arrives on the row as `linked_leg`, so
  // this no longer needs a transaction that is not on the page.
  const transferBadgeByPostingId = useMemo(
    () => buildTransferBadges(rows, accounts, transferLinks, realIds),
    [rows, accounts, transferLinks, realIds],
  )
  // The posting whose transfer-detail popup is open, or `null` — looked up
  // against `transferBadgeByPostingId` at render time rather than storing
  // the resolved info itself, so the popup always reflects the latest data.
  const [transferDetailPostingId, setTransferDetailPostingId] = useState<string | null>(null)
  const handleOpenTransferDetail = useCallback((postingId: string) => setTransferDetailPostingId(postingId), [])
  const transferDetail = transferDetailPostingId
    ? (transferBadgeByPostingId.get(transferDetailPostingId) ?? null)
    : null

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
  const categoryOptions = useMemo(
    () => [
      { id: UNCATEGORIZED, name: 'Uncategorized' },
      ...topLevelCategories.map((c) => ({ id: c.category_id, name: c.name })),
    ],
    [topLevelCategories],
  )
  const monthItems = useMemo(
    () => ({
      [ALL_MONTHS]: 'All months',
      ...Object.fromEntries((months ?? []).map((month) => [month, formatMonthLong(month)])),
    }),
    [months],
  )
  const subcategoryOptions = useMemo(
    () => [
      { id: NO_SUBCATEGORY, name: 'None' },
      ...subcategories.map((c) => ({ id: c.category_id, name: `${c.parentName} › ${c.name}` })),
    ],
    [subcategories],
  )
  const tagFilterOptions = useMemo(() => tagOptions.map((t) => ({ id: t.tag_id, name: t.name })), [tagOptions])

  const withSubcategories = useMemo(() => categoriesWithSubcategories(categories), [categories])
  const ruleLabelById = useMemo(
    () => new Map(rules.map((rule) => [rule.rule_id, rule.description || rule.description_contains])),
    [rules],
  )

  // Placeholder legs ride along on the page for the transfer badge and for
  // "mark as transfer", and are never a row of their own — the same exclusion
  // `projection.filter_predicates` applies to the filter and to every count.
  const visible = useMemo(() => rows.filter((posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)), [rows])
  // Scored against the rows on this page rather than the whole collection.
  // PR E recorded this as an accepted behaviour change, and it is not a silent
  // one: the banner says so, an unmatched counterpart elsewhere shows no hint
  // rather than a wrong one, and widening the filter is the way to it — which
  // is already what it was for a counterpart an account filter had hidden.
  const pickHints = useMemo(() => pickHintByPostingId(visible, pickingSource), [visible, pickingSource])
  const pendingOnPage = useMemo(() => visible.filter((posting) => posting.pending_source !== null), [visible])
  const allPendingOnPageSelected =
    pendingOnPage.length > 0 && pendingOnPage.every((posting) => posting.pending_selected)
  const somePendingOnPageSelected = pendingOnPage.some((posting) => posting.pending_selected)

  function handleValidatePending() {
    validateFiltered(query, (result) =>
      toast.success(
        // `matched` is the set the action ran over, not what it changed — a
        // matching row with no pending suggestion is skipped. Naming it
        // "resolved" read as though every one of them had been.
        `Checked ${result.matched.toLocaleString()} matching row${result.matched === 1 ? '' : 's'} — ` +
          `${result.accepted.toLocaleString()} accepted, ${result.reverted.toLocaleString()} reverted.`,
      ),
    )
  }

  // `needs_categorizing` is what the button's own count is of, so it is part
  // of the filter the action is sent with. Without it the server would widen
  // the set to every row the bar matches, categorized ones included.
  const bulkTargetFilter = useMemo(() => ({ ...query, needs_categorizing: true }), [query])

  function handleRunBulkPatternSuggest() {
    runBulkPatternSuggest(bulkTargetFilter, (result) =>
      toast.success(
        `Matched ${result.matched.toLocaleString()} row${result.matched === 1 ? '' : 's'} — ` +
          `${result.applied.toLocaleString()} got a suggestion.`,
      ),
    )
  }

  function handleToggleSelectAllOnPage() {
    const nextSelected = !allPendingOnPageSelected
    for (const posting of pendingOnPage) {
      if (posting.pending_selected !== nextSelected) handleToggleSelected(posting.posting_id, nextSelected)
    }
  }

  // Only the rows actually scrolled into view get mounted. A page is bounded
  // now, but 200 transactions is still up to 200 live category dropdowns.
  const scrollParentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 12,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  const selectAllRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = somePendingOnPageSelected && !allPendingOnPageSelected
    }
  }, [somePendingOnPageSelected, allPendingOnPageSelected])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <TransactionsToolbar
          onlyUncategorized={onlyUncategorized}
          matchedPostings={counts?.matched_postings ?? 0}
          bulkTargetCount={counts?.needs_categorizing ?? 0}
          pendingCount={counts?.pending ?? 0}
          pendingSelectedCount={counts?.pending_selected ?? 0}
          bulkProgress={bulkProgress}
          bulkSuggesting={bulkSuggesting}
          bulkPatternSuggesting={bulkPatternSuggesting}
          aiAvailable={aiAvailable}
          validatePendingIsPending={validatePendingIsPending}
          onBulkAiSuggest={() => runBulkAiSuggest(bulkTargetFilter)}
          onBulkPatternSuggest={handleRunBulkPatternSuggest}
          onValidatePending={handleValidatePending}
        />
        <TransactionsFilterBar
          filters={persisted}
          setFilters={setStoredFilters}
          search={search}
          onSearchChange={setSearch}
          monthItems={monthItems}
          accountItems={accountItems}
          categoryOptions={categoryOptions}
          subcategoryOptions={subcategoryOptions}
          tagFilterOptions={tagFilterOptions}
        />
      </CardHeader>
      <CardContent>
        {pickingSource && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-green-500/40 bg-green-100 px-3 py-2 text-sm dark:bg-green-950/40">
            <span>
              Pick the transaction that matches{' '}
              <span className="font-medium tabular-nums">
                {formatCurrency(-pickingSource.amount, pickingSource.currency)}
              </span>{' '}
              for this transfer. Only this page is scored — widen the filter or page on to reach the rest.
            </span>
            <Button variant="outline" size="sm" onClick={handleCancelPicking}>
              Cancel
            </Button>
          </div>
        )}
        {page.isError ? (
          // Never an empty table on a failed read: a list that is missing rows
          // because the request failed looks exactly like a list with none.
          <p className="py-6 text-center text-sm text-destructive">
            Couldn’t load transactions — this list would otherwise be missing rows without saying so.
          </p>
        ) : visible.length === 0 && !page.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {onlyUncategorized ? 'Nothing left to categorize.' : 'No transactions match — import a statement to start.'}
          </p>
        ) : (
          <div
            ref={scrollParentRef}
            className={`max-h-[70vh] overflow-x-auto overflow-y-auto ${stale ? 'opacity-60' : ''}`}
            aria-busy={stale}
          >
            <Table>
              <TransactionsTableHead
                sort={sort}
                toggleSort={(key) =>
                  setSort((prev) => (prev.key === key ? { key, desc: !prev.desc } : { key, desc: true }))
                }
                selectAllRef={selectAllRef}
                pendingOnPageCount={pendingOnPage.length}
                allPendingOnPageSelected={allPendingOnPageSelected}
                onToggleSelectAllOnPage={handleToggleSelectAllOnPage}
              />
              <TableBody>
                {paddingTop > 0 && (
                  <tr>
                    <td colSpan={TABLE_COLUMN_COUNT} style={{ height: paddingTop }} />
                  </tr>
                )}
                {virtualRows.map((virtualRow) => {
                  const posting = visible[virtualRow.index]
                  return (
                    <TransactionRow
                      key={posting.posting_id}
                      ref={rowVirtualizer.measureElement}
                      data-index={virtualRow.index}
                      posting={posting}
                      accountName={accounts[posting.account_id]?.name ?? posting.account_id}
                      resolvedByRuleLabel={
                        posting.resolved_by_transfer_rule_id && posting.is_real_income_expense
                          ? (ruleLabelById.get(posting.resolved_by_transfer_rule_id) ??
                            posting.resolved_by_transfer_rule_id)
                          : null
                      }
                      isRealIncomeExpense={posting.is_real_income_expense}
                      categories={categories}
                      tags={tags}
                      withSubcategories={withSubcategories}
                      aiMessage={suggestMessages[posting.posting_id]}
                      aiPending={aiPending}
                      aiAvailable={aiAvailable}
                      safeCounterpartyAccounts={safeCounterpartyAccounts}
                      pickHint={pickHints?.get(posting.posting_id) ?? null}
                      transferBadge={transferBadgeByPostingId.get(posting.posting_id) ?? null}
                      onOverride={handleOverride}
                      onAiSuggest={runAiSuggest}
                      onSplit={setSplitting}
                      onUndoSplit={handleUndoSplit}
                      onToggleSelected={handleToggleSelected}
                      onMarkAsTransfer={handleMarkAsTransfer}
                      onExcludeFromRule={handleExcludeFromRule}
                      onStartPicking={handleStartPicking}
                      onPickTarget={handlePickTarget}
                      onOpenTransferDetail={handleOpenTransferDetail}
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
        {!page.isError && (
          <TablePagination
            offset={offset}
            limit={PAGE_SIZE}
            total={total}
            unit="transaction"
            busy={stale}
            onOffsetChange={setOffset}
          />
        )}
      </CardContent>
      {splitting && (
        <PostingSplitDialog posting={splitting} categories={categories} onClose={() => setSplitting(null)} />
      )}
      {transferDetail && (
        <TransferDetailDialog
          badge={transferDetail}
          ruleLabelById={ruleLabelById}
          onClose={() => setTransferDetailPostingId(null)}
          onUnlinkTransfer={handleUnlinkTransfer}
          onUndoManualOverride={handleUndoManualOverride}
          onExcludeFromRule={handleExcludeFromRule}
          onExcludeAndUnlinkFromRule={handleExcludeAndUnlinkFromRule}
        />
      )}
    </Card>
  )
}
