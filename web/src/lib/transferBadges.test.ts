import { describe, expect, it } from 'vitest'
import { transferBadgeByPostingId } from '@/lib/transferBadges'
import type { Account, Posting, TransferLink } from '@/types/accounting'

function makeAccount(accountId: string, kind: Account['kind'], name: string): Account {
  return { account_id: accountId, kind, name, institution: 'Test Bank', currency: 'USD', closed: false } as Account
}

const ACCOUNTS: Record<string, Account> = {
  checking: makeAccount('checking', 'checking', 'Everyday'),
  savings: makeAccount('savings', 'savings', 'Rainy Day'),
  cash: makeAccount('cash', 'cash', 'Wallet'),
  'uncategorized:expense': makeAccount('uncategorized:expense', 'expense_payee', 'Uncategorized'),
}

function makePosting(overrides: Partial<Posting> & Pick<Posting, 'posting_id' | 'transaction_id'>): Posting {
  return {
    account_id: 'checking',
    posted_at: '2026-01-15T00:00:00',
    amount: -25,
    currency: 'USD',
    description: 'Moved money',
    category_id: null,
    subcategory_id: null,
    tag_ids: [],
    pending_source: null,
    pending_selected: true,
    is_linked_transfer: false,
    ...overrides,
  } as Posting
}

describe('transferBadgeByPostingId', () => {
  it('gives no badge to an ordinary posting', () => {
    const postings = [
      makePosting({ posting_id: 'p1', transaction_id: 't1' }),
      makePosting({ posting_id: 'p2', transaction_id: 't1', account_id: 'uncategorized:expense', amount: 25 }),
    ]

    expect(transferBadgeByPostingId(postings, ACCOUNTS, [], new Set(['p1'])).size).toBe(0)
  })

  it('never badges a placeholder leg, which is never rendered as a row', () => {
    const postings = [
      makePosting({
        posting_id: 'ghost',
        transaction_id: 't1',
        account_id: 'uncategorized:expense',
        manual_transfer_override_posting_id: 'ghost',
      }),
    ]

    expect(transferBadgeByPostingId(postings, ACCOUNTS, [], new Set()).size).toBe(0)
  })

  describe('a confirmed link', () => {
    const link: TransferLink = { link_id: 'l1', transaction_id_a: 't1', transaction_id_b: 't2' } as TransferLink
    const postings = [
      makePosting({
        posting_id: 'out',
        transaction_id: 't1',
        account_id: 'checking',
        amount: -100,
        is_linked_transfer: true,
        linked_transaction_id: 't2',
        transfer_link_source: 'manual',
        linked_leg: {
          transaction_id: 't2',
          account_id: 'savings',
          description: 'Moved money',
          posted_at: '2026-01-15T00:00:00',
          amount: 100,
          currency: 'USD',
        },
      }),
      makePosting({
        posting_id: 'in',
        transaction_id: 't2',
        account_id: 'savings',
        amount: 100,
        is_linked_transfer: true,
        linked_transaction_id: 't1',
        transfer_link_source: 'manual',
        linked_leg: {
          transaction_id: 't1',
          account_id: 'checking',
          description: 'Moved money',
          posted_at: '2026-01-15T00:00:00',
          amount: -100,
          currency: 'USD',
        },
      }),
    ]

    it('names the other side by its account, in the direction the money moved', () => {
      const badges = transferBadgeByPostingId(postings, ACCOUNTS, [link], new Set())

      expect(badges.get('out')?.label).toBe('Transfer to Rainy Day')
      expect(badges.get('in')?.label).toBe('Transfer from Everyday')
    })

    it('orders the popup from the leg the money left to the leg it arrived at', () => {
      const popup = transferBadgeByPostingId(postings, ACCOUNTS, [link], new Set()).get('in')?.popup

      expect(popup?.kind).toBe('link')
      if (popup?.kind !== 'link') return
      expect(popup.from.accountName).toBe('Everyday')
      expect(popup.to.accountName).toBe('Rainy Day')
      expect(popup.source).toBe('manual')
      expect(popup.ruleId).toBeNull()
    })

    // The partner used to have to be in the same array, so a page boundary or
    // an account filter cost a genuinely-linked row its badge. `linked_leg` is
    // joined onto the row server-side precisely so it does not.
    it('badges a row whose partner is not in the list at all', () => {
      const orphan = [postings[0]]

      expect(transferBadgeByPostingId(orphan, ACCOUNTS, [link], new Set()).get('out')?.label).toBe(
        'Transfer to Rainy Day',
      )
    })

    // What "absent" means now: the server found no real leg for the partner —
    // its statement was re-imported away, or the ledger was rebuilt. A
    // half-resolved badge would name the wrong side.
    it('gives no badge when the server sent no partner leg', () => {
      const orphan = [makePosting({ ...postings[0], linked_leg: null })]

      expect(transferBadgeByPostingId(orphan, ACCOUNTS, [link], new Set()).size).toBe(0)
    })
  })

  it('names the account a manual repoint points at', () => {
    const postings = [
      makePosting({
        posting_id: 'real',
        transaction_id: 't1',
        account_id: 'checking',
        amount: -40,
        manual_transfer_override_posting_id: 'placeholder',
      }),
      makePosting({ posting_id: 'placeholder', transaction_id: 't1', account_id: 'cash', amount: 40 }),
    ]

    const badge = transferBadgeByPostingId(postings, ACCOUNTS, [], new Set()).get('real')

    expect(badge?.label).toBe('Transfer to Wallet')
    expect(badge?.popup).toEqual({ kind: 'override', postingId: 'placeholder', otherAccountName: 'Wallet' })
  })

  describe('a rule that repointed the counterparty directly', () => {
    const postings = [
      makePosting({
        posting_id: 'real',
        transaction_id: 't1',
        account_id: 'checking',
        amount: -40,
        resolved_by_transfer_rule_id: 'r1',
      }),
      makePosting({
        posting_id: 'other',
        transaction_id: 't1',
        account_id: 'cash',
        amount: 40,
        resolved_by_transfer_rule_id: 'r1',
      }),
    ]

    it('badges the leg that is not real income or expense', () => {
      const badge = transferBadgeByPostingId(postings, ACCOUNTS, [], new Set()).get('real')

      expect(badge?.label).toBe('Transfer to Wallet')
      expect(badge?.popup).toMatchObject({ kind: 'direct-rule', ruleId: 'r1', transactionId: 't1' })
    })

    // A rule pointing at a virtual counterparty is plain categorization, not a
    // transfer, and keeps its "via rule" tag in the account column instead.
    it('gives no badge to a leg that is real income or expense', () => {
      const badges = transferBadgeByPostingId(postings, ACCOUNTS, [], new Set(['real']))

      expect(badges.has('real')).toBe(false)
    })
  })

  // A manual override is applied after rules, so the server never sets both on
  // one transaction — but the order here has to agree with that regardless.
  it('prefers a link over a manual repoint over a rule', () => {
    const link: TransferLink = { link_id: 'l1', transaction_id_a: 't1', transaction_id_b: 't2' } as TransferLink
    const postings = [
      makePosting({
        posting_id: 'out',
        transaction_id: 't1',
        account_id: 'checking',
        amount: -100,
        is_linked_transfer: true,
        linked_transaction_id: 't2',
        transfer_link_source: 'rule',
        manual_transfer_override_posting_id: 'somewhere',
        resolved_by_transfer_rule_id: 'r1',
        linked_leg: {
          transaction_id: 't2',
          account_id: 'savings',
          description: 'Moved money',
          posted_at: '2026-01-15T00:00:00',
          amount: 100,
          currency: 'USD',
        },
      }),
      makePosting({ posting_id: 'in', transaction_id: 't2', account_id: 'savings', amount: 100 }),
    ]

    expect(transferBadgeByPostingId(postings, ACCOUNTS, [link], new Set()).get('out')?.popup.kind).toBe('link')
  })
})
