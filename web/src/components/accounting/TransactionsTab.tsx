import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowRightLeft, Pencil, RotateCcw, Scissors, Sparkles, Undo2 } from 'lucide-react'
import { memo, type Ref, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { PostingSplitDialog } from '@/components/accounting/PostingSplitDialog'
import { TagsCell } from '@/components/accounting/TagsCell'
import { TransferRowCard } from '@/components/accounting/TransferRowCard'
import {
  categoriesWithSubcategories,
  needsCategorizing,
  splitOriginalId,
} from '@/components/accounting/transactionCategorization'
import { CounterpartySelect } from '@/components/shared/CounterpartySelect'
import { FilterPanel, FilterRow } from '@/components/shared/FilterPanel'
import { FilterSelect } from '@/components/shared/FilterSelect'
import { MultiSelectFilter } from '@/components/shared/MultiSelectFilter'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useAiSuggestCategory,
  useCreateTransferLink,
  useDeletePostingSplit,
  useLlmUsage,
  usePatchTransferRule,
  usePatternSuggestCategory,
  usePatternSuggestCategoryBulk,
  useRemoveTransferLink,
  useSetPostingOverride,
  useValidatePending,
} from '@/hooks/useAccountingData'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSortableRows } from '@/hooks/useSortableRows'
import { safeDirectRepointOptions } from '@/lib/counterpartyAccounts'
import { FILTER_ALL as ALL } from '@/lib/filters'
import { formatCurrency, formatDate, formatMonthLong } from '@/lib/format'
import { anyLlmProviderAvailable } from '@/lib/llm'
import { availableMonths } from '@/lib/months'
import { type PickHint, pickHintByPostingId } from '@/lib/pickHints'
import { realIncomeExpensePostingIds } from '@/lib/postingClassification'
import {
  ALL_MONTHS,
  activeFilterCount as countActiveFilters,
  DATE_MODE_MONTH,
  DATE_MODE_RANGE,
  defaultFilterState,
  type FilterState,
  filterPostings,
  NO_SUBCATEGORY,
  normalizeFilterState,
  PENDING_OPTIONS,
  PLACEHOLDER_ACCOUNT_IDS,
  TRANSFER_FLAG_OPTIONS,
  UNCATEGORIZED,
} from '@/lib/transactionFilters'
import { transferBadgeByPostingId as buildTransferBadges, type TransferBadgeInfo } from '@/lib/transferBadges'
import { TRANSFER_UNLINK_WARNING_PAIR } from '@/lib/transferRowInfo'
import { addedExcludedTransactionIds, ruleUpdateFromRule } from '@/lib/transferRules'
import type { Account, Category, ManualOverride, Posting, Tag, TransferLink, TransferRule } from '@/types/accounting'

// Approximate row height (px) the virtualizer reserves before measuring the
// real one — a table row with `p-2 text-sm` cells lands around here.
const ESTIMATED_ROW_HEIGHT = 45
const TABLE_COLUMN_COUNT = 9

const INCOME_EXPENSE_ITEMS: Record<string, string> = { [ALL]: 'All', income: 'Income', expense: 'Expense' }
const CATEGORIZED_ITEMS: Record<string, string> = {
  [ALL]: 'All',
  categorized: 'Categorized',
  uncategorized: 'Uncategorized',
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
  aiAvailable: boolean
  safeCounterpartyAccounts: Account[]
  pickHint: PickHint | null
  transferBadge: TransferBadgeInfo | null
  onOverride: (postingId: string, override: Partial<ManualOverride>) => void
  onAiSuggest: (posting: Posting) => void
  onSplit: (posting: Posting) => void
  onUndoSplit: (originalPostingId: string) => void
  onToggleSelected: (postingId: string, selected: boolean) => void
  onMarkAsTransfer: (transactionId: string, counterpartyAccountId: string) => void
  onExcludeFromRule: (transactionIds: string[], ruleId: string) => void
  onStartPicking: (posting: Posting) => void
  onPickTarget: (posting: Posting) => void
  onOpenTransferDetail: (postingId: string) => void
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
  aiAvailable,
  safeCounterpartyAccounts,
  pickHint,
  transferBadge,
  onOverride,
  onAiSuggest,
  onSplit,
  onUndoSplit,
  onToggleSelected,
  onMarkAsTransfer,
  onExcludeFromRule,
  onStartPicking,
  onPickTarget,
  onOpenTransferDetail,
}: TransactionRowProps) {
  const originalId = splitOriginalId(posting.posting_id)
  const pendingClass = posting.pending_source ? PENDING_ROW_CLASS[posting.pending_source] : undefined
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [showAccountFallback, setShowAccountFallback] = useState(false)
  // A transaction already split into categorized legs must never be
  // silently excluded from income/expense by a link formed afterward (see
  // `ledger.transfers.reconcile_rule_links`'s own guard, mirrored here) —
  // the split-leg row itself is what `originalId` names.
  const alreadySplit = originalId !== null
  // Skipping straight into picking mode when there's no external/safe
  // account to offer as the other option — a 2-choice popup with only one
  // real choice is a pointless extra click.
  function openFlagAsTransfer() {
    if (safeCounterpartyAccounts.length === 0) {
      onStartPicking(posting)
      return
    }
    setShowAccountFallback(false)
    setTransferDialogOpen(true)
  }
  // While a table-wide pick is active, every row that isn't the source
  // itself gets styled/gated by its own eligibility (see `pickHintByPostingId`
  // in `TransactionsTable`) — the source row and an eligible target both
  // stay visually distinct from a row that can never be picked (already
  // linked/split, or an amount that can't match), which is greyed out.
  const pickRowClass =
    pickHint?.eligibility === 'source'
      ? 'ring-1 ring-inset ring-green-500/50 bg-green-100 dark:bg-green-950/40'
      : pickHint?.eligibility === 'eligible'
        ? 'cursor-pointer hover:bg-muted/70'
        : pickHint?.eligibility === 'already-linked' ||
            pickHint?.eligibility === 'already-split' ||
            pickHint?.eligibility === 'amount-mismatch'
          ? 'opacity-50'
          : undefined
  const rowClassName = [pendingClass, pickRowClass].filter(Boolean).join(' ') || undefined
  return (
    <TableRow
      ref={ref}
      data-index={dataIndex}
      className={rowClassName}
      title={pickHint?.tooltip}
      onClick={pickHint?.eligibility === 'eligible' ? () => onPickTarget(posting) : undefined}
    >
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
      <TableCell className="whitespace-nowrap text-muted-foreground">
        {formatDate(posting.posted_at.slice(0, 10))}
      </TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">
        {accountName}
        {/* Only for a rule whose counterparty is virtual (income_source/
            expense_payee) — plain categorization provenance, not a
            transfer. The real-account-counterparty case gets the
            "Transfer to/from X" badge in the category column instead (see
            `transferBadgeByPostingId`'s `direct-rule` case) — `resolvedByRuleLabel`
            is already `null` for that case, so this never double-shows. */}
        {resolvedByRuleLabel && (
          <span className="ml-1 inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            <span title={`Resolved by rule: ${resolvedByRuleLabel} — deleting that rule reverts this posting`}>
              via rule
            </span>
            {posting.resolved_by_transfer_rule_id && (
              <button
                type="button"
                className="-mr-0.5 inline-flex size-4 items-center justify-center rounded-sm hover:bg-background hover:text-foreground"
                title={`Exclude this one transaction from "${resolvedByRuleLabel}" — it'll fall back to the next-matching rule, or stay uncategorized if none matches`}
                onClick={() =>
                  posting.resolved_by_transfer_rule_id &&
                  onExcludeFromRule([posting.transaction_id], posting.resolved_by_transfer_rule_id)
                }
              >
                ×
              </button>
            )}
          </span>
        )}
        {pickHint?.eligibility === 'source' && (
          <span className="ml-1 text-[10px] text-muted-foreground">picking a match…</span>
        )}
      </TableCell>
      <TableCell className="max-w-[200px]">
        <Truncate text={posting.description} />
      </TableCell>
      <TableCell className="text-right tabular-nums">{formatCurrency(posting.amount, posting.currency)}</TableCell>
      <TableCell>
        {isRealIncomeExpense ? (
          <CategorySelect
            categories={categories}
            classification={posting.amount >= 0 ? 'income' : 'expense'}
            value={posting.category_id ?? null}
            onChange={(categoryId) =>
              // Changing category always clears subcategory — it's a child
              // of the OLD category, never carried over. A category with
              // subcategories stays in "Needs categorizing" until one is
              // picked too (see `needsCategorizing`), so this row
              // deliberately doesn't disappear yet.
              onOverride(posting.posting_id, { category_id: categoryId, subcategory_id: null })
            }
          />
        ) : transferBadge ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted/70 hover:text-foreground"
            title="View this transfer"
            onClick={() => onOpenTransferDetail(posting.posting_id)}
          >
            {transferBadge.label}
            <Pencil className="size-3" />
          </button>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        {isRealIncomeExpense ? (
          <SubcategorySelect
            categories={categories}
            categoryId={posting.category_id ?? null}
            value={posting.subcategory_id ?? null}
            onChange={(subcategoryId) => onOverride(posting.posting_id, { subcategory_id: subcategoryId })}
          />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <TagsCell
          tagIds={posting.tag_ids ?? []}
          tags={tags}
          onChange={(tagIds) => onOverride(posting.posting_id, { tag_ids: tagIds })}
        />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-0.5">
          {isRealIncomeExpense && (
            <Button variant="ghost" size="icon" title="Mark as transfer" onClick={openFlagAsTransfer}>
              <ArrowRightLeft className="size-3.5 text-muted-foreground" />
            </Button>
          )}
          {needsCategorizing(posting, withSubcategories, isRealIncomeExpense) && (
            <Button
              variant="ghost"
              size="icon"
              title={aiMessage || (aiAvailable ? 'AI suggestion' : 'No AI provider configured — add a key in Settings')}
              disabled={aiPending || !aiAvailable}
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
      {isRealIncomeExpense && (
        <Dialog open={transferDialogOpen} onOpenChange={setTransferDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Flag as transfer</DialogTitle>
            </DialogHeader>
            {alreadySplit ? (
              <p className="text-sm text-muted-foreground">
                Already split into categorized legs — can't be linked as a transfer.
              </p>
            ) : showAccountFallback ? (
              <div className="flex flex-col gap-2">
                <CounterpartySelect
                  accounts={safeCounterpartyAccounts}
                  value={null}
                  onChange={(accountId) => {
                    if (accountId) onMarkAsTransfer(posting.transaction_id, accountId)
                    setTransferDialogOpen(false)
                  }}
                />
                <Button variant="ghost" size="sm" className="self-start" onClick={() => setShowAccountFallback(false)}>
                  Back
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-sm text-muted-foreground">
                  Link this to another transaction you've already imported, or point it at one of your own non-bank
                  accounts (cash, a loan, a payee).
                </p>
                <Button
                  variant="outline"
                  onClick={() => {
                    onStartPicking(posting)
                    setTransferDialogOpen(false)
                  }}
                >
                  Link to another transaction…
                </Button>
                <Button variant="outline" onClick={() => setShowAccountFallback(true)}>
                  Point at an account instead
                </Button>
              </div>
            )}
            <DialogFooter>
              <Button variant="ghost" onClick={() => setTransferDialogOpen(false)}>
                Cancel
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </TableRow>
  )
})

// The popup a category-column transfer badge opens on click — exactly one
// of the three shapes `TransferBadgeInfo.popup` can hold. Its own component
// (rather than inline JSX) so each branch can pull the fields it needs into
// plain local consts once, instead of repeating `badge.popup.foo` property
// access inside click handlers (which TypeScript can't narrow the same way
// a local const's own type gets narrowed).
// Reads clearly above the button it warns about, rather than as a vague
// aside below it — spelled out concretely (re-linking is a manual redo,
// not a click away) instead of the ambiguous "there is no going back".
const UNMARK_WARNING_SINGLE =
  "This can't be undone automatically — you'd have to manually flag this transaction again if you change your mind."

function TransferDetailDialog({
  badge,
  ruleLabelById,
  onClose,
  onUnlinkTransfer,
  onUndoManualOverride,
  onExcludeFromRule,
  onExcludeAndUnlinkFromRule,
}: {
  badge: TransferBadgeInfo
  ruleLabelById: Map<string, string | undefined>
  onClose: () => void
  onUnlinkTransfer: (linkId: string) => void
  onUndoManualOverride: (postingId: string) => void
  onExcludeFromRule: (transactionIds: string[], ruleId: string) => void
  onExcludeAndUnlinkFromRule: (transactionIds: string[], ruleId: string, linkId: string) => void
}) {
  const { popup } = badge
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Transfer</DialogTitle>
        </DialogHeader>
        {popup.kind === 'override' ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes into the external account <span className="font-medium">{popup.otherAccountName}</span>
              .
            </p>
            <p className="text-xs text-muted-foreground">{UNMARK_WARNING_SINGLE}</p>
            <Button
              variant="destructive"
              onClick={() => {
                onUndoManualOverride(popup.postingId)
                onClose()
              }}
            >
              Unmark as transfer
            </Button>
          </div>
        ) : popup.kind === 'direct-rule' ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes from <span className="font-medium">{popup.from.accountName}</span> to{' '}
              <span className="font-medium">{popup.to.accountName}</span>, and is part of rule{' '}
              <Link className="underline hover:text-foreground" to={`/rules?tab=rules&ruleId=${popup.ruleId}`}>
                {ruleLabelById.get(popup.ruleId) ?? popup.ruleId}
              </Link>
              .
            </p>
            <div className="flex flex-col items-stretch gap-1">
              <TransferRowCard row={popup.from} />
              <ArrowDown className="mx-auto size-4 shrink-0 text-muted-foreground" />
              <TransferRowCard row={popup.to} />
            </div>
            <Button
              variant="destructive"
              onClick={() => {
                onExcludeFromRule([popup.transactionId], popup.ruleId)
                onClose()
              }}
            >
              Exclude this specific transfer from rule {ruleLabelById.get(popup.ruleId) ?? popup.ruleId}
            </Button>
            <p className="text-xs text-muted-foreground">
              The excluded transfers can be found{' '}
              <Link className="underline hover:text-foreground" to={`/rules?tab=excluded&ruleId=${popup.ruleId}`}>
                here
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes from <span className="font-medium">{popup.from.accountName}</span> to{' '}
              <span className="font-medium">{popup.to.accountName}</span>
              {popup.source === 'rule' && popup.ruleId
                ? (() => {
                    const ruleId = popup.ruleId
                    return (
                      <>
                        {' '}
                        and is part of rule{' '}
                        <Link className="underline hover:text-foreground" to={`/rules?tab=rules&ruleId=${ruleId}`}>
                          {ruleLabelById.get(ruleId) ?? ruleId}
                        </Link>
                      </>
                    )
                  })()
                : null}
              .
            </p>
            <div className="flex flex-col items-stretch gap-1">
              <TransferRowCard row={popup.from} />
              <ArrowDown className="mx-auto size-4 shrink-0 text-muted-foreground" />
              <TransferRowCard row={popup.to} />
            </div>
            {popup.source === 'rule' && popup.ruleId ? (
              (() => {
                const ruleId = popup.ruleId
                const fromTransactionId = popup.from.transactionId
                const toTransactionId = popup.to.transactionId
                return (
                  <>
                    <Button
                      variant="destructive"
                      onClick={() => {
                        onExcludeAndUnlinkFromRule([fromTransactionId, toTransactionId], ruleId, popup.linkId)
                        onClose()
                      }}
                    >
                      Exclude this specific transfer from rule {ruleLabelById.get(ruleId) ?? ruleId}
                    </Button>
                    <p className="text-xs text-muted-foreground">
                      Both transactions go back to being normal transactions. The excluded transfers can be found{' '}
                      <Link className="underline hover:text-foreground" to={`/rules?tab=excluded&ruleId=${ruleId}`}>
                        here
                      </Link>
                      .
                    </p>
                  </>
                )
              })()
            ) : (
              <>
                <p className="text-xs text-muted-foreground">{TRANSFER_UNLINK_WARNING_PAIR}</p>
                <Button
                  variant="destructive"
                  onClick={() => {
                    onUnlinkTransfer(popup.linkId)
                    onClose()
                  }}
                >
                  Unmark as transfer
                </Button>
              </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function TransactionsTable({
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
  const [stored, setFilters] = usePersistedState<FilterState>(storageKey, defaultFilterState())
  // Whatever vintage of filter state `localStorage` holds is coerced into a
  // usable one in one place — see `normalizeFilterState` for what a stale
  // single-select value used to do to a `.includes` check.
  const filters = useMemo(() => normalizeFilterState(stored), [stored])
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
  const patchTransferRule = usePatchTransferRule()
  const createTransferLink = useCreateTransferLink()
  const removeTransferLink = useRemoveTransferLink()
  const { data: llmUsage } = useLlmUsage()
  const aiAvailable = anyLlmProviderAvailable(llmUsage)
  // Only ever a fallback now — the primary "flag as transfer" path links to
  // a specific other transaction instead (see `pickHintByPostingId` below).
  // Repointing straight onto an `IMPORTABLE_ACCOUNT_KINDS` account (the
  // ones this list used to include) risks double-counting against that
  // account's own independently-imported statement.
  const safeCounterpartyAccounts = useMemo(() => safeDirectRepointOptions(accounts), [accounts])
  // The transaction currently being matched against, or `null` when no pick
  // is in progress — set by clicking "Link to another transaction…" on a
  // row, cleared by picking a target (or Cancel). Table-wide, not per-row
  // local state, since every OTHER row's own styling/clickability depends
  // on it (see `pickHintByPostingId`).
  const [pickingSource, setPickingSource] = useState<Posting | null>(null)
  // The placeholder leg of each transaction — never rendered as its own
  // row (see `filtered` below) but needed here to know *which* posting a
  // "mark as transfer" override actually has to target: the visible row is
  // always the real leg, but repointing a transfer's counterparty means
  // overriding the still-placeholder sibling, not the real leg itself.
  const placeholderPostingIdByTransactionId = useMemo(() => {
    const lookup = new Map<string, string>()
    for (const posting of postings) {
      if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) lookup.set(posting.transaction_id, posting.posting_id)
    }
    return lookup
  }, [postings])
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
    } catch (error) {
      // Already surfaced via the global mutation-error toast (see App.tsx) —
      // logged here too so a failure is distinguishable from "nothing needed
      // suggesting" when debugging.
      console.error('Bulk pattern suggestion failed', error)
    } finally {
      setBulkPatternSuggesting(false)
    }
  }

  const handleOverride = useCallback(
    (postingId: string, override: Partial<ManualOverride>) => setOverride.mutate({ postingId, override }),
    [setOverride],
  )
  const handleMarkAsTransfer = useCallback(
    (transactionId: string, counterpartyAccountId: string) => {
      const placeholderPostingId = placeholderPostingIdByTransactionId.get(transactionId)
      if (!placeholderPostingId) return
      setOverride.mutate({ postingId: placeholderPostingId, override: { account_id: counterpartyAccountId } })
    },
    [setOverride, placeholderPostingIdByTransactionId],
  )
  // Clears just the manual account override, restoring this posting to
  // whatever it would resolve to without it (a rule, a link, or plain
  // uncategorized) — the same "explicit null clears just this one field"
  // merge semantics `put_posting_override` already has for every other field.
  const handleUndoManualOverride = useCallback(
    (postingId: string) => setOverride.mutate({ postingId, override: { account_id: null } }),
    [setOverride],
  )
  // Takes every transaction id to exclude in one call (both sides of a
  // rule-found transfer link, or just the one transaction for a direct
  // single-account repoint) so this rule only gets patched once — a
  // second, separate `PATCH` for the same rule fired before this one's
  // response lands would be a genuine conflict on that row, caught (409)
  // by the rule's own `expected_version`; there's no reason to invite it.
  const handleExcludeFromRule = useCallback(
    (transactionIds: string[], ruleId: string) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      const ruleLabel = rule.description || rule.description_contains || ruleId
      patchTransferRule.mutate(
        {
          ruleId,
          update: ruleUpdateFromRule(rule, {
            excluded_transaction_ids: addedExcludedTransactionIds(rule, transactionIds),
          }),
        },
        {
          onSuccess: () =>
            toast.success(
              transactionIds.length > 1
                ? `Excluded from "${ruleLabel}" — both transactions fall back to the next-matching rule, or stay uncategorized. Manage exclusions from the Rules page.`
                : `Excluded from "${ruleLabel}" — this transaction falls back to the next-matching rule, or stays uncategorized. Manage exclusions from the Rules page.`,
            ),
        },
      )
    },
    [rules, patchTransferRule],
  )
  // Excluding a rule-found link must also delete the `TransferLink` itself
  // so both transactions actually revert to normal (see
  // `TransferDetailDialog`'s "Exclude this specific transfer…" button).
  // The two calls touch disjoint rows — the rule patch governed by its own
  // `expected_version` (see `usePatchTransferRule`), the unlink naming one
  // link by id — so they can't conflict with each other. Awaited in
  // sequence purely so the success toast only fires once both have landed.
  const handleExcludeAndUnlinkFromRule = useCallback(
    async (transactionIds: string[], ruleId: string, linkId: string) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      const ruleLabel = rule.description || rule.description_contains || ruleId
      await patchTransferRule.mutateAsync({
        ruleId,
        update: ruleUpdateFromRule(rule, {
          excluded_transaction_ids: addedExcludedTransactionIds(rule, transactionIds),
        }),
      })
      await removeTransferLink.mutateAsync(linkId)
      toast.success(`Excluded from "${ruleLabel}" — both transactions are back to being normal transactions.`)
    },
    [rules, patchTransferRule, removeTransferLink],
  )
  const handleLinkTransfer = useCallback(
    (transactionIdA: string, transactionIdB: string) => {
      createTransferLink.mutate({ transaction_id_a: transactionIdA, transaction_id_b: transactionIdB })
    },
    [createTransferLink],
  )
  const handleUnlinkTransfer = useCallback((linkId: string) => removeTransferLink.mutate(linkId), [removeTransferLink])
  const handleStartPicking = useCallback((posting: Posting) => setPickingSource(posting), [])
  const handleCancelPicking = useCallback(() => setPickingSource(null), [])
  const handlePickTarget = useCallback(
    (target: Posting) => {
      if (!pickingSource) return
      handleLinkTransfer(pickingSource.transaction_id, target.transaction_id)
      setPickingSource(null)
    },
    [pickingSource, handleLinkTransfer],
  )
  const handleUndoSplit = useCallback(
    (originalPostingId: string) => deleteSplit.mutate(originalPostingId),
    [deleteSplit],
  )
  const handleToggleSelected = useCallback(
    (postingId: string, selected: boolean) =>
      setOverride.mutate({ postingId, override: { pending_selected: selected } }),
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
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  const selectAllRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = somePendingSelected && !allPendingSelected
  }, [somePendingSelected, allPendingSelected])

  const activeFilterCount = useMemo(() => countActiveFilters(filters), [filters])

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{onlyUncategorized ? 'Needs categorizing' : 'All transactions'}</CardTitle>
          <span className="text-xs text-muted-foreground">
            {sorted.length.toLocaleString()} transaction{sorted.length === 1 ? '' : 's'}
          </span>
          {bulkTargets.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              disabled={bulkSuggesting || !aiAvailable}
              title={aiAvailable ? undefined : 'No AI provider configured — add a key in Settings'}
              onClick={() => runBulkAiSuggest(bulkTargets)}
            >
              <Sparkles className="size-3.5" />
              {bulkProgress
                ? `Suggesting ${bulkProgress.done}/${bulkProgress.total}…`
                : `AI suggest all (${bulkTargets.length})`}
            </Button>
          )}
          {bulkTargets.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              disabled={bulkPatternSuggesting}
              onClick={() => runBulkPatternSuggest(bulkTargets)}
            >
              <Sparkles className="size-3.5" />
              {bulkPatternSuggesting ? 'Matching patterns…' : `Run pattern suggestions (${bulkTargets.length})`}
            </Button>
          )}
          {pendingInView.length > 0 && (
            <Button variant="outline" size="sm" disabled={validatePending.isPending} onClick={handleValidateSelection}>
              Validate selection ({checkedPendingCount}/{pendingInView.length})
            </Button>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {activeFilterCount > 0 && (
            <Button variant="ghost" size="sm" onClick={() => setFilters(defaultFilterState())}>
              <RotateCcw className="size-3.5" />
              Reset filters
            </Button>
          )}
          <Input
            className="w-48"
            placeholder="Search description…"
            value={filters.search}
            onChange={(event) => setFilters({ ...filters, search: event.target.value })}
          />
          <FilterPanel activeCount={activeFilterCount}>
            <FilterRow label="Date">
              <div className="flex items-center gap-1.5">
                <Button
                  type="button"
                  variant={filters.dateMode === DATE_MODE_MONTH ? 'default' : 'outline'}
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setFilters({ ...filters, dateMode: DATE_MODE_MONTH })}
                >
                  Month
                </Button>
                <Button
                  type="button"
                  variant={filters.dateMode === DATE_MODE_RANGE ? 'default' : 'outline'}
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setFilters({ ...filters, dateMode: DATE_MODE_RANGE })}
                >
                  Range
                </Button>
              </div>
              {filters.dateMode === DATE_MODE_MONTH ? (
                <Select value={filters.month} onValueChange={(month) => month && setFilters({ ...filters, month })}>
                  <SelectTrigger size="sm" className="mt-1.5 w-full">
                    <SelectValue items={monthItems} />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(monthItems).map(([id, name]) => (
                      <SelectItem key={id} value={id}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <div className="mt-1.5 flex flex-col gap-1.5">
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
                </div>
              )}
            </FilterRow>
            <FilterRow label="Account">
              <FilterSelect
                value={filters.accountFilter}
                exclude={filters.accountExclude}
                items={accountItems}
                width="w-full"
                onValueChange={(value) => setFilters({ ...filters, accountFilter: value })}
                onExcludeChange={(exclude) => setFilters({ ...filters, accountExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="Category">
              <MultiSelectFilter
                label="Category"
                options={categoryOptions}
                selected={filters.categoryFilter}
                exclude={filters.categoryExclude}
                onSelectedChange={(next) => setFilters({ ...filters, categoryFilter: next })}
                onExcludeChange={(exclude) => setFilters({ ...filters, categoryExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="Subcategory">
              <MultiSelectFilter
                label="Subcategory"
                options={subcategoryOptions}
                selected={filters.subcategoryFilter}
                exclude={filters.subcategoryExclude}
                onSelectedChange={(next) => setFilters({ ...filters, subcategoryFilter: next })}
                onExcludeChange={(exclude) => setFilters({ ...filters, subcategoryExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="Tag">
              <MultiSelectFilter
                label="Tag"
                options={tagFilterOptions}
                selected={filters.tagFilter}
                exclude={filters.tagExclude}
                onSelectedChange={(next) => setFilters({ ...filters, tagFilter: next })}
                onExcludeChange={(exclude) => setFilters({ ...filters, tagExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="AI/pattern suggestion status">
              <MultiSelectFilter
                label="Status"
                options={PENDING_OPTIONS}
                selected={filters.pendingFilter}
                exclude={filters.pendingExclude ?? false}
                onSelectedChange={(next) => setFilters({ ...filters, pendingFilter: next })}
                onExcludeChange={(exclude) => setFilters({ ...filters, pendingExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="Filter transfers">
              <MultiSelectFilter
                label="Filter transfers"
                options={TRANSFER_FLAG_OPTIONS}
                selected={filters.transferFlagFilter}
                exclude={filters.transferFlagExclude}
                onSelectedChange={(next) => setFilters({ ...filters, transferFlagFilter: next })}
                onExcludeChange={(exclude) => setFilters({ ...filters, transferFlagExclude: exclude })}
              />
            </FilterRow>
            <FilterRow label="Income / expense">
              <Select
                value={filters.incomeExpenseFilter ?? ALL}
                onValueChange={(value) => value && setFilters({ ...filters, incomeExpenseFilter: value })}
              >
                <SelectTrigger size="sm" className="w-full">
                  <SelectValue items={INCOME_EXPENSE_ITEMS} />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(INCOME_EXPENSE_ITEMS).map(([id, name]) => (
                    <SelectItem key={id} value={id}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FilterRow>
            <FilterRow label="Categorized">
              <Select
                value={filters.categorizedFilter ?? ALL}
                onValueChange={(value) => value && setFilters({ ...filters, categorizedFilter: value })}
              >
                <SelectTrigger size="sm" className="w-full">
                  <SelectValue items={CATEGORIZED_ITEMS} />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CATEGORIZED_ITEMS).map(([id, name]) => (
                    <SelectItem key={id} value={id}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FilterRow>
          </FilterPanel>
        </div>
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
                  <SortableTableHead
                    active={sort.key === 'posted_at'}
                    desc={sort.desc}
                    onClick={() => toggleSort('posted_at')}
                  >
                    Date
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'account_id'}
                    desc={sort.desc}
                    onClick={() => toggleSort('account_id')}
                  >
                    Account
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'description'}
                    desc={sort.desc}
                    onClick={() => toggleSort('description')}
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
                    active={sort.key === 'category_id'}
                    desc={sort.desc}
                    onClick={() => toggleSort('category_id')}
                  >
                    Category
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'subcategory_id'}
                    desc={sort.desc}
                    onClick={() => toggleSort('subcategory_id')}
                  >
                    Subcategory
                  </SortableTableHead>
                  <SortableTableHead
                    active={sort.key === 'tag_ids'}
                    desc={sort.desc}
                    onClick={() => toggleSort('tag_ids')}
                  >
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
                      aiPending={
                        aiSuggest.isPending || bulkSuggesting || patternSuggest.isPending || bulkPatternSuggesting
                      }
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
