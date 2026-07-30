import { ArrowRightLeft, Pencil, Scissors, Sparkles, Undo2 } from 'lucide-react'
import { memo, type Ref, useState } from 'react'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { TagsCell } from '@/components/accounting/TagsCell'
import { needsCategorizing, splitOriginalId } from '@/components/accounting/transactionCategorization'
import { CounterpartySelect } from '@/components/shared/CounterpartySelect'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { TableCell, TableRow } from '@/components/ui/table'
import { formatCurrency, formatDate } from '@/lib/format'
import type { PickHint } from '@/lib/pickHints'
import type { TransferBadgeInfo } from '@/lib/transferBadges'
import type { Account, Category, ManualOverride, Posting, Tag } from '@/types/accounting'

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
export const TransactionRow = memo(function TransactionRow({
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
