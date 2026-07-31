import type { TransferRule, TransferRuleUpdate } from '@/types/accounting'

// Builds a `PATCH /transfer-rules/{rule_id}` body from a rule's current
// state plus whichever fields are actually changing — every caller
// (`TransferRulesTab`, `ExcludedFromRulesTab`, `TransactionsTab`) edits
// exactly one field of a rule at a time but the endpoint takes the whole
// mutable field set per request, matching `AccountUpdate`'s convention.
// `expected_version` defaults to the rule's own `version` (a real
// optimistic-concurrency check for destructive field edits), but a caller
// may override it — the `active`-toggle path passes `expected_version:
// null` to opt into last-write-wins, since fast-flipping a switch should
// never 409 against its own earlier click (see
// `docs/optimistic-concurrency-versioning.md`).
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
    expected_version: rule.version,
    ...overrides,
  }
}

// Shared by every "exclude from rule" action (`TransactionsTab`'s
// transfer-detail popup, `TransferRulesTab`'s "linked by this rule" table) —
// a plain, pure array transform, no I/O of its own.
export function addedExcludedTransactionIds(rule: TransferRule, transactionIds: string[]): string[] {
  return [...new Set([...(rule.excluded_transaction_ids ?? []), ...transactionIds])]
}
