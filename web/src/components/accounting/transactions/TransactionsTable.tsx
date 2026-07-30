import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PostingSplitDialog } from '@/components/accounting/PostingSplitDialog'
import { categoriesWithSubcategories, needsCategorizing } from '@/components/accounting/transactionCategorization'
import { TransactionRow } from '@/components/accounting/transactions/TransactionRow'
import { TransactionsFilterBar } from '@/components/accounting/transactions/TransactionsFilterBar'
import { TransactionsTableHead } from '@/components/accounting/transactions/TransactionsTableHead'
import { TransactionsToolbar } from '@/components/accounting/transactions/TransactionsToolbar'
import { TransferDetailDialog } from '@/components/accounting/transactions/TransferDetailDialog'
import { useTransactionActions } from '@/components/accounting/transactions/useTransactionActions'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Table, TableBody } from '@/components/ui/table'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSortableRows } from '@/hooks/useSortableRows'
import { FILTER_ALL as ALL } from '@/lib/filters'
import { formatCurrency, formatMonthLong } from '@/lib/format'
import { availableMonths } from '@/lib/months'
import { pickHintByPostingId } from '@/lib/pickHints'
import { realIncomeExpensePostingIds } from '@/lib/postingClassification'
import {
  ALL_MONTHS,
  defaultFilterState,
  filterPostings,
  NO_SUBCATEGORY,
  normalizeFilterState,
  type PersistedFilters,
  PLACEHOLDER_ACCOUNT_IDS,
  UNCATEGORIZED,
  withSearch,
} from '@/lib/transactionFilters'
import { transferBadgeByPostingId as buildTransferBadges } from '@/lib/transferBadges'
import type { Account, Category, Posting, Tag, TransferLink, TransferRule } from '@/types/accounting'

// Approximate row height (px) the virtualizer reserves before measuring the
// real one — a table row with `p-2 text-sm` cells lands around here.
const ESTIMATED_ROW_HEIGHT = 45
const TABLE_COLUMN_COUNT = 9
// Long enough that a burst of typing settles once, short enough that the table
// still feels like it is tracking the box.
const SEARCH_DEBOUNCE_MS = 200

export function TransactionsTable({
  storageKey,
  postings,
  accounts,
  categories,
  tags,
  rules,
  transferLinks,
  onlyUncategorized,
}: {
  storageKey: string
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
  rules: TransferRule[]
  transferLinks: TransferLink[]
  onlyUncategorized: boolean
}) {
  const [stored, setFilters] = usePersistedState<PersistedFilters>(storageKey, defaultFilterState())
  // Whatever vintage of filter state `localStorage` holds is coerced into a
  // usable one in one place — see `normalizeFilterState` for what a stale
  // single-select value used to do to a `.includes` check.
  const persisted = useMemo(() => normalizeFilterState(stored), [stored])
  // The search term is component state rather than persisted state, and the
  // filter reads it a beat behind the box. Typing then costs a controlled
  // input's own re-render instead of a `JSON.stringify`, a `localStorage`
  // write and a re-filter of the whole ledger per keystroke — see
  // `PersistedFilters` and `useDebouncedValue`.
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebouncedValue(search, SEARCH_DEBOUNCE_MS)
  const filters = useMemo(() => withSearch(persisted, debouncedSearch), [persisted, debouncedSearch])
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
    validatePendingIds,
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
  } = useTransactionActions(postings, accounts, rules)
  // Computed from the full, unscoped `postings` prop (not `filtered`/`sorted`)
  // — an account filter could otherwise split a transfer pair apart and make
  // sibling-detection wrong. See `postingClassification.ts`.
  const realIds = useMemo(() => realIncomeExpensePostingIds(postings, accounts), [postings, accounts])
  // Everything the category-column badge and its detail popup need for a
  // linked, manually-overridden, or rule-direct-repointed transaction.
  const transferBadgeByPostingId = useMemo(
    () => buildTransferBadges(postings, accounts, transferLinks, realIds),
    [postings, accounts, transferLinks, realIds],
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
      ...Object.fromEntries(availableMonths(postings).map((month) => [month, formatMonthLong(month)])),
    }),
    [postings],
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
  // Every transaction id excluded from at least one rule — a fact
  // independent of the transaction's *current* transfer status (it can be
  // excluded from a rule and also currently a plain non-transfer, or
  // excluded from one rule while a different rule still flags it), so it's
  // its own filter option rather than folded into "non transfer".
  const excludedTransactionIds = useMemo(
    () => new Set(rules.flatMap((rule) => rule.excluded_transaction_ids ?? [])),
    [rules],
  )

  const filtered = useMemo(
    () =>
      filterPostings(postings, filters, {
        onlyUncategorized,
        withSubcategories,
        realIncomeExpensePostingIds: realIds,
        excludedTransactionIds,
      }),
    [postings, filters, onlyUncategorized, withSubcategories, realIds, excludedTransactionIds],
  )

  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'posted_at')
  const pickHints = useMemo(() => pickHintByPostingId(sorted, pickingSource), [sorted, pickingSource])
  const bulkTargets = useMemo(
    () => filtered.filter((posting) => needsCategorizing(posting, withSubcategories, realIds.has(posting.posting_id))),
    [filtered, withSubcategories, realIds],
  )
  const pendingInView = useMemo(() => sorted.filter((posting) => posting.pending_source !== null), [sorted])
  const allPendingSelected = pendingInView.length > 0 && pendingInView.every((posting) => posting.pending_selected)
  const somePendingSelected = pendingInView.some((posting) => posting.pending_selected)
  const checkedPendingCount = useMemo(
    () => pendingInView.filter((posting) => posting.pending_selected).length,
    [pendingInView],
  )

  function handleValidateSelection() {
    validatePendingIds(pendingInView.map((posting) => posting.posting_id))
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
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  const selectAllRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = somePendingSelected && !allPendingSelected
  }, [somePendingSelected, allPendingSelected])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <TransactionsToolbar
          onlyUncategorized={onlyUncategorized}
          visibleCount={sorted.length}
          bulkTargetCount={bulkTargets.length}
          bulkProgress={bulkProgress}
          bulkSuggesting={bulkSuggesting}
          bulkPatternSuggesting={bulkPatternSuggesting}
          aiAvailable={aiAvailable}
          pendingInViewCount={pendingInView.length}
          checkedPendingCount={checkedPendingCount}
          validatePendingIsPending={validatePendingIsPending}
          onBulkAiSuggest={() => runBulkAiSuggest(bulkTargets)}
          onBulkPatternSuggest={() => runBulkPatternSuggest(bulkTargets)}
          onValidateSelection={handleValidateSelection}
        />
        <TransactionsFilterBar
          filters={persisted}
          setFilters={setFilters}
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
              for this transfer.
            </span>
            <Button variant="outline" size="sm" onClick={handleCancelPicking}>
              Cancel
            </Button>
          </div>
        )}
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {onlyUncategorized ? 'Nothing left to categorize.' : 'No transactions match — import a statement to start.'}
          </p>
        ) : (
          <div ref={scrollParentRef} className="max-h-[70vh] overflow-x-auto overflow-y-auto">
            <Table>
              <TransactionsTableHead
                sort={sort}
                toggleSort={toggleSort}
                selectAllRef={selectAllRef}
                pendingInViewCount={pendingInView.length}
                allPendingSelected={allPendingSelected}
                onToggleSelectAllPending={handleToggleSelectAllPending}
              />
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
                        posting.resolved_by_transfer_rule_id && realIds.has(posting.posting_id)
                          ? (ruleLabelById.get(posting.resolved_by_transfer_rule_id) ??
                            posting.resolved_by_transfer_rule_id)
                          : null
                      }
                      isRealIncomeExpense={realIds.has(posting.posting_id)}
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
