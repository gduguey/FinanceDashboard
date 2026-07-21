import type { TransferRule, TransferRuleUpdate } from '@/types/accounting'

// Builds a `PATCH /transfer-rules/{rule_id}` body from a rule's current
// state plus whichever fields are actually changing — every caller
// (`TransferRulesTab`, `ExcludedFromRulesTab`, `TransactionsTab`) edits
// exactly one field of a rule at a time but the endpoint takes the whole
// mutable field set per request, matching `AccountUpdate`'s convention.
// `expected_version` always comes from `rule` itself, never from an
// override, since a caller extending its own optimistic patch's `active`/
// `priority` guess in `overrides` should never also get to guess the
// version it's checked against.
export function ruleUpdateFromRule(
  rule: TransferRule,
  overrides: Partial<TransferRuleUpdate> = {},
): TransferRuleUpdate {
  return {
    description_contains: rule.description_contains,
    account_id: rule.account_id,
    counterparty_account_id: rule.counterparty_account_id,
    priority: rule.priority,
    description: rule.description,
    active: rule.active,
    excluded_transaction_ids: rule.excluded_transaction_ids ?? [],
    ...overrides,
    expected_version: rule.version,
  }
}

// Shared by every "exclude from rule" action (`TransactionsTab`'s
// transfer-detail popup, `TransferRulesTab`'s "linked by this rule" table) —
// a plain, pure array transform, no I/O of its own.
export function addedExcludedTransactionIds(rule: TransferRule, transactionIds: string[]): string[] {
  return [...new Set([...(rule.excluded_transaction_ids ?? []), ...transactionIds])]
}
