import type { Account, Posting } from '@/types/accounting'

const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

// A cent of float/rounding slack, never a real discrepancy — an actually
// mismatched-fee pair should never silently count as a match.
const AMOUNT_TOLERANCE = 0.01

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

// One confirmed (or reconstructed) transfer pair, flattened so every
// sortable column (a date, an account name, an amount, on either side) is
// its own top-level field — `useSortableRows` only ever sorts by a single
// top-level key, never a nested path like `from.postedAt`.
export interface LinkedPairRow {
  linkId: string
  fromTransactionId: string
  fromAccountName: string
  fromDescription: string
  fromPostedAt: string
  fromAmount: number
  fromCurrency: string
  toTransactionId: string
  toAccountName: string
  toDescription: string
  toPostedAt: string
  toAmount: number
  toCurrency: string
}

function toLinkedPairRow(linkId: string, from: TransferRowInfo, to: TransferRowInfo): LinkedPairRow {
  return {
    linkId,
    fromTransactionId: from.transactionId,
    fromAccountName: from.accountName,
    fromDescription: from.description,
    fromPostedAt: from.postedAt,
    fromAmount: from.amount,
    fromCurrency: from.currency,
    toTransactionId: to.transactionId,
    toAccountName: to.accountName,
    toDescription: to.description,
    toPostedAt: to.postedAt,
    toAmount: to.amount,
    toCurrency: to.currency,
  }
}

// Reconstructs from/to pairs out of a flat list of rows with no persisted
// link between them — used for a rule's excluded transactions, which name
// transaction ids but never which two form one transfer (excluding a rule
// match never creates a `TransferLink` to look that up in). Greedily pairs
// each row with the first unmatched opposite-sign, same-magnitude row it
// finds; anything left over (a rule whose counterparty is a single
// non-importable account only ever excludes one transaction, never two)
// falls through to `singles`.
export function pairTransferRows(rows: TransferRowInfo[]): { pairs: LinkedPairRow[]; singles: TransferRowInfo[] } {
  const remaining = [...rows]
  const pairs: LinkedPairRow[] = []
  const singles: TransferRowInfo[] = []
  while (remaining.length > 0) {
    const row = remaining.shift() as TransferRowInfo
    const matchIndex = remaining.findIndex(
      (candidate) => candidate.currency === row.currency && Math.abs(candidate.amount + row.amount) < AMOUNT_TOLERANCE,
    )
    if (matchIndex === -1) {
      singles.push(row)
      continue
    }
    const [match] = remaining.splice(matchIndex, 1) as [TransferRowInfo]
    const [from, to] = row.amount < 0 ? [row, match] : [match, row]
    pairs.push(toLinkedPairRow(`${from.transactionId}:${to.transactionId}`, from, to))
  }
  return { pairs, singles }
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
