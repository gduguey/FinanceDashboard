import type { AccountKind } from '@/types/accounting'

// The one display name for each `AccountKind`, used everywhere the app
// shows a kind to a user — the account-creation form, every accounts
// table, the net worth composition bar — so the same kind never reads as
// a different string (or a raw snake_case value) depending on which page
// happens to render it.
export const ACCOUNT_KIND_LABELS: Record<AccountKind, string> = {
  checking: 'Checking',
  savings: 'Savings',
  credit_card: 'Credit Card',
  vault: 'Vault',
  cash: 'Cash',
  loan: 'Loan',
  income_source: 'Income Source',
  expense_payee: 'Expense Payee',
  external_investment: 'External Investment',
  other_asset: 'Other Asset',
}

// The coarser grouping the net worth composition bar shows — a handful
// of buckets meaningful at a glance, rather than every one of the ten
// underlying kinds. `income_source`/`expense_payee` are the two virtual
// placeholder kinds every posting starts pointed at (see
// `accounting.models.AccountKind`) and never represent real money of
// their own, so they're excluded rather than grouped into anything.
export type AccountKindGroup = 'Cash' | 'Savings' | 'Investment' | 'Other assets' | 'Loan'

const ACCOUNT_KIND_GROUPS: Partial<Record<AccountKind, AccountKindGroup>> = {
  checking: 'Cash',
  credit_card: 'Cash',
  cash: 'Cash',
  savings: 'Savings',
  vault: 'Savings',
  external_investment: 'Investment',
  other_asset: 'Other assets',
  loan: 'Loan',
}

export function accountKindGroup(kind: AccountKind): AccountKindGroup | null {
  return ACCOUNT_KIND_GROUPS[kind] ?? null
}
