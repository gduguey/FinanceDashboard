import type { Account, Posting } from '@/types/accounting'

const VIRTUAL_ACCOUNT_KINDS = new Set(['income_source', 'expense_payee'])

// True once the user has added at least one account of their own — the
// two virtual placeholder counterparties every fresh install seeds
// (`uncategorized:expense`/`uncategorized:income`, see `accounting.taxonomy`)
// don't count, since they never represent money the user actually has.
// Takes any iterable of account-shaped rows (the full `Account` store, or a
// lighter `NetWorthAccountRow` list) rather than one specific type, since
// both callers only ever need `kind`.
export function hasAnyRealAccount(accounts: Iterable<{ kind: string }>): boolean {
  return [...accounts].some((account) => !VIRTUAL_ACCOUNT_KINDS.has(account.kind))
}

// Mirrors the backend's own `_real_income_expense_legs` test (see
// `dashboard.income_statement`): a posting only ever represents real
// income or a real expense — as opposed to an internal transfer between
// two accounts you hold — when it's not itself on a virtual placeholder
// account AND its transaction's sibling leg is. Anything else (both legs
// on real accounts) is an internal transfer, and is never categorizable —
// there's no "kind of spend" to assign it.
//
// Built from `allPostings` (every posting, unscoped) rather than an
// already-filtered subset — an account filter in particular can drop one
// leg of a pair, which would otherwise make its sibling look
// virtual/non-virtual incorrectly.
export function realIncomeExpensePostingIds(allPostings: Posting[], accounts: Record<string, Account>): Set<string> {
  const virtualAccountIds = new Set(
    Object.values(accounts)
      .filter((account) => VIRTUAL_ACCOUNT_KINDS.has(account.kind))
      .map((account) => account.account_id),
  )
  const transactionHasVirtualLeg = new Map<string, boolean>()
  for (const posting of allPostings) {
    const isVirtual = virtualAccountIds.has(posting.account_id)
    if (isVirtual) transactionHasVirtualLeg.set(posting.transaction_id, true)
  }
  const ids = new Set<string>()
  for (const posting of allPostings) {
    if (virtualAccountIds.has(posting.account_id)) continue
    if (!transactionHasVirtualLeg.get(posting.transaction_id)) continue
    // Excludes a confirmed `TransferLink` the same way the backend's own
    // chokepoint does — a linked transaction stays excluded from
    // income/expense regardless of which account either leg's placeholder
    // still points at (see `ledger.transfers.apply_transfer_links`).
    if (posting.is_linked_transfer) continue
    ids.add(posting.posting_id)
  }
  return ids
}
