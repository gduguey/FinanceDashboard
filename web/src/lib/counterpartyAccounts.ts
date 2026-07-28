import type { Account } from '@/types/accounting'

// The two placeholder counterparties every posting starts pointed at (see
// `accounting.store.UNCATEGORIZED_EXPENSE_ACCOUNT_ID`/`UNCATEGORIZED_INCOME_ACCOUNT_ID`)
// aren't things a transfer rule (or a manual "mark as transfer") ever
// repoints a posting *to* — the whole point is repointing a posting away
// from one of these, so they're excluded from the counterparty picker.
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

// Mirrors `accounting.models.IMPORTABLE_ACCOUNT_KINDS` — every kind with a
// registered CSV standardizer, so it might already have its own,
// independently-imported posting for the same event. A `TransferRule`
// naming one of these as its counterparty never repoints a placeholder
// straight onto it (see `ledger.categorization.apply_rules`); it's instead
// resolved safely, via a `TransferLink`, once a unique matching transaction
// is found (see `ledger.transfers.reconcile_rule_links`). Everything else
// (a virtual payee, or a real account nothing is ever independently
// imported for) stays a safe, direct repoint target.
const IMPORTABLE_ACCOUNT_KINDS = new Set(['checking', 'savings', 'credit_card', 'vault'])

export function counterpartyOptions(accounts: Record<string, Account>): Account[] {
  return Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id) && !account.closed)
    .sort((a, b) => a.name.localeCompare(b.name))
}

// The subset of `counterpartyOptions` a rule can safely repoint a
// placeholder straight onto — never `checking`/`savings`/`credit_card`/
// `vault`, which risk double-counting against that account's own
// independently-imported statement (see `IMPORTABLE_ACCOUNT_KINDS` above).
export function safeDirectRepointOptions(accounts: Record<string, Account>): Account[] {
  return counterpartyOptions(accounts).filter((account) => !IMPORTABLE_ACCOUNT_KINDS.has(account.kind))
}

export function needsLinkingAccount(account: Account | undefined): boolean {
  return account !== undefined && IMPORTABLE_ACCOUNT_KINDS.has(account.kind)
}
