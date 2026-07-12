import type { Account, Posting } from '@/types/accounting'

const VIRTUAL_ACCOUNT_KINDS = new Set(['income_source', 'expense_payee'])

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
    Object.values(accounts).filter((account) => VIRTUAL_ACCOUNT_KINDS.has(account.kind)).map((account) => account.account_id),
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
    ids.add(posting.posting_id)
  }
  return ids
}
