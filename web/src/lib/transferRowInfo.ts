import type { Account, Posting } from '@/types/accounting'

const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

// One side of a transfer, reduced to only what a recognizable summary needs
// (date/account/description/amount) — shared by the Transactions page's
// transfer-detail popup and the Rules page's linked/excluded transaction
// tables, so every place that renders "one transaction as a small card"
// uses the exact same shape.
export interface TransferRowInfo {
  transactionId: string
  accountName: string
  description: string
  postedAt: string
  amount: number
  currency: string
}

// Each transaction's own real (non-placeholder) leg — the row actually
// shown for it everywhere in the UI, since a placeholder never renders as
// its own row. Built from an unscoped posting list so a filter elsewhere
// can't hide the leg a transfer's counterpart needs to look up.
export function realLegByTransactionId(
  postings: Posting[],
  accounts: Record<string, Account>,
): Map<string, TransferRowInfo> {
  const lookup = new Map<string, TransferRowInfo>()
  for (const posting of postings) {
    if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) continue
    lookup.set(posting.transaction_id, {
      transactionId: posting.transaction_id,
      accountName: accounts[posting.account_id]?.name ?? posting.account_id,
      description: posting.description,
      postedAt: posting.posted_at,
      amount: posting.amount,
      currency: posting.currency,
    })
  }
  return lookup
}
