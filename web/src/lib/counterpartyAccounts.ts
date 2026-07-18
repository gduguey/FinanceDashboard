import type { Account } from '@/types/accounting'

// The two placeholder counterparties every posting starts pointed at (see
// `accounting.store.UNCATEGORIZED_EXPENSE_ACCOUNT_ID`/`UNCATEGORIZED_INCOME_ACCOUNT_ID`)
// aren't things a transfer rule (or a manual "mark as transfer") ever
// repoints a posting *to* — the whole point is repointing a posting away
// from one of these, so they're excluded from the counterparty picker.
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

export function counterpartyOptions(accounts: Record<string, Account>): Account[] {
  return Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    .sort((a, b) => a.name.localeCompare(b.name))
}
