import { PLACEHOLDER_ACCOUNT_IDS } from '@/lib/transactionFilters'
import {
  realLegByTransactionId as buildRealLegByTransactionId,
  siblingLegByPostingId as buildSiblingLegByPostingId,
  type TransferRowInfo,
} from '@/lib/transferRowInfo'
import type { Account, Posting, TransferLink } from '@/types/accounting'

// Everything the category-column "Transfer …" badge and its detail popup
// need — computed once per posting, covering all three mechanisms that put a
// badge there: a `TransferLink` (manual or rule-found), a manual "point at an
// account" `ManualOverride.account_id`, and `apply_rules`'s own direct repoint
// onto a safe (non-importable) *real* account — never onto a virtual
// `income_source`/`expense_payee` counterparty, since that's plain
// income/expense categorization, not a transfer, and keeps its own "via rule"
// tag in the account column instead.
export interface TransferBadgeInfo {
  label: string
  popup:
    | {
        kind: 'link'
        source: 'manual' | 'rule'
        ruleId: string | null
        linkId: string
        from: TransferRowInfo
        to: TransferRowInfo
      }
    | { kind: 'override'; postingId: string; otherAccountName: string }
    | { kind: 'direct-rule'; ruleId: string; transactionId: string; from: TransferRowInfo; to: TransferRowInfo }
}

/**
 * The transfer badge for every posting that has one, keyed by posting id.
 *
 * Keyed by the real-leg row that is actually rendered; placeholders never are,
 * and are skipped. Direction is one rule across all three mechanisms: "to"
 * when this posting's own amount is negative (money leaving), "from" when
 * positive (money arriving).
 *
 * Built from the full, unscoped posting list rather than the filtered one. A
 * transfer's counterpart is usually on a different account, so an account
 * filter would otherwise leave a genuinely-linked row with no badge.
 *
 * @param postings - Every resolved posting, unfiltered.
 * @param accounts - The store's accounts, keyed by id.
 * @param transferLinks - Every confirmed transfer link.
 * @param realIncomeExpensePostingIds - Postings that are real income/expense
 *   rather than an internal transfer. A rule-repointed posting only gets a
 *   badge when it is *not* in this set.
 * @returns One entry per posting that should show a badge.
 */
export function transferBadgeByPostingId(
  postings: Posting[],
  accounts: Record<string, Account>,
  transferLinks: TransferLink[],
  realIncomeExpensePostingIds: Set<string>,
): Map<string, TransferBadgeInfo> {
  const realLegByTransactionId = buildRealLegByTransactionId(postings, accounts)
  const siblingLegByPostingId = buildSiblingLegByPostingId(postings, accounts)
  const linkByTransactionId = new Map<string, TransferLink>()
  for (const link of transferLinks) {
    linkByTransactionId.set(link.transaction_id_a, link)
    linkByTransactionId.set(link.transaction_id_b, link)
  }
  // Whatever account a manually-overridden placeholder currently sits on —
  // looked up by the posting id `manual_transfer_override_posting_id` names,
  // so the "manual" badge can name the account by something a person
  // recognizes instead of a raw posting id.
  const accountNameByPostingId = new Map<string, string>()
  for (const posting of postings) {
    accountNameByPostingId.set(posting.posting_id, accounts[posting.account_id]?.name ?? posting.account_id)
  }

  const lookup = new Map<string, TransferBadgeInfo>()
  for (const posting of postings) {
    if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) continue
    const direction = posting.amount < 0 ? 'to' : 'from'
    if (posting.is_linked_transfer && posting.linked_transaction_id) {
      const link = linkByTransactionId.get(posting.transaction_id)
      const other = realLegByTransactionId.get(posting.linked_transaction_id)
      const mine = realLegByTransactionId.get(posting.transaction_id)
      if (!link || !other || !mine) continue
      const [from, to] = posting.amount < 0 ? [mine, other] : [other, mine]
      lookup.set(posting.posting_id, {
        label: `Transfer ${direction} ${other.accountName}`,
        popup: {
          kind: 'link',
          source: posting.transfer_link_source === 'rule' ? 'rule' : 'manual',
          ruleId: link.rule_id ?? null,
          linkId: link.link_id,
          from,
          to,
        },
      })
    } else if (posting.manual_transfer_override_posting_id) {
      const otherAccountName =
        accountNameByPostingId.get(posting.manual_transfer_override_posting_id) ?? 'another account'
      lookup.set(posting.posting_id, {
        label: `Transfer ${direction} ${otherAccountName}`,
        popup: { kind: 'override', postingId: posting.manual_transfer_override_posting_id, otherAccountName },
      })
    } else if (posting.resolved_by_transfer_rule_id && !realIncomeExpensePostingIds.has(posting.posting_id)) {
      // Only when this posting ISN'T real income/expense — a rule whose
      // counterparty is virtual (income_source/expense_payee) is plain
      // categorization, not a transfer, and keeps its own "via rule" tag
      // in the account column instead.
      const sibling = siblingLegByPostingId.get(posting.posting_id)
      if (!sibling) continue
      const mine: TransferRowInfo = {
        transactionId: posting.transaction_id,
        accountName: accounts[posting.account_id]?.name ?? posting.account_id,
        description: posting.description,
        postedAt: posting.posted_at,
        amount: posting.amount,
        currency: posting.currency,
      }
      const [from, to] = posting.amount < 0 ? [mine, sibling] : [sibling, mine]
      lookup.set(posting.posting_id, {
        label: `Transfer ${direction} ${sibling.accountName}`,
        popup: {
          kind: 'direct-rule',
          ruleId: posting.resolved_by_transfer_rule_id,
          transactionId: posting.transaction_id,
          from,
          to,
        },
      })
    }
  }
  return lookup
}
