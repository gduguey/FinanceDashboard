import { splitOriginalId } from '@/components/accounting/transactionCategorization'
import { formatCurrency } from '@/lib/format'
import type { Posting } from '@/types/accounting'

// How close two amounts have to be to count as "the same transfer, opposite
// sides" — a cent of float/rounding slack, never a real discrepancy (an
// actually mismatched-fee pair should show as a mismatch, not silently link).
export const AMOUNT_TOLERANCE = 0.01

// The manual "flag as transfer" flow never auto-matches a counterparty —
// that's what `TransferSuggestionsPanel`'s own heuristic is for. Picking a
// target transaction is instead an explicit, table-wide mode: clicking
// "Link to another transaction…" on one row turns every other row into a
// pick target, scored against that one row's own criteria.
export type PickEligibility = 'source' | 'eligible' | 'already-linked' | 'already-split' | 'amount-mismatch'

export interface PickHint {
  eligibility: PickEligibility
  tooltip?: string
}

/**
 * Score every visible row against the transaction a transfer is being matched to.
 *
 * Scoped to the rows currently on screen rather than the full posting list — a
 * transfer's counterpart is almost always on a different account, so an active
 * account filter can hide it, and "Reset filters" is the way out of that, same
 * as it would be for finding any other hidden transaction.
 *
 * @param visible - The rows currently rendered, in render order.
 * @param source - The transaction being matched, or `null` when no pick is in
 *   progress.
 * @returns One entry per visible row, or `null` entirely when nothing is being
 *   picked — so a row can tell "not picking" apart from "picking, but this row
 *   was never scored", which should never happen.
 */
export function pickHintByPostingId(visible: Posting[], source: Posting | null): Map<string, PickHint> | null {
  if (!source) return null
  const neededAmount = -source.amount
  const lookup = new Map<string, PickHint>()
  for (const posting of visible) {
    if (posting.transaction_id === source.transaction_id) {
      lookup.set(posting.posting_id, { eligibility: 'source' })
      continue
    }
    if (posting.is_linked_transfer) {
      lookup.set(posting.posting_id, {
        eligibility: 'already-linked',
        tooltip: 'Already linked to another transaction',
      })
      continue
    }
    if (splitOriginalId(posting.posting_id) !== null) {
      lookup.set(posting.posting_id, {
        eligibility: 'already-split',
        tooltip: "Already split into categorized legs — can't be linked",
      })
      continue
    }
    const matches = posting.currency === source.currency && Math.abs(posting.amount - neededAmount) < AMOUNT_TOLERANCE
    lookup.set(posting.posting_id, {
      eligibility: matches ? 'eligible' : 'amount-mismatch',
      tooltip: matches ? undefined : `Amounts don't match — needs ${formatCurrency(neededAmount, source.currency)}`,
    })
  }
  return lookup
}
