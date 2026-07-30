import { describe, expect, it } from 'vitest'
import { pickHintByPostingId } from '@/lib/pickHints'
import type { Posting } from '@/types/accounting'

function makePosting(overrides: Partial<Posting> & Pick<Posting, 'posting_id'>): Posting {
  return {
    transaction_id: `t:${overrides.posting_id}`,
    account_id: 'checking',
    posted_at: '2026-01-15T00:00:00',
    amount: 100,
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

const SOURCE = makePosting({ posting_id: 'source', transaction_id: 'src', amount: -100 })

function eligibilityOf(postings: Posting[]): Record<string, string> {
  const hints = pickHintByPostingId(postings, SOURCE)
  return Object.fromEntries([...(hints ?? [])].map(([id, hint]) => [id, hint.eligibility]))
}

describe('pickHintByPostingId', () => {
  it('scores nothing when no pick is in progress', () => {
    expect(pickHintByPostingId([makePosting({ posting_id: 'p1' })], null)).toBeNull()
  })

  it('marks every leg of the source transaction as the source', () => {
    const postings = [
      makePosting({ posting_id: 'source', transaction_id: 'src', amount: -100 }),
      makePosting({ posting_id: 'source-other', transaction_id: 'src', amount: 100 }),
    ]

    expect(eligibilityOf(postings)).toEqual({ source: 'source', 'source-other': 'source' })
  })

  it('accepts the opposite amount in the same currency', () => {
    const postings = [makePosting({ posting_id: 'match', amount: 100 })]

    expect(eligibilityOf(postings)).toEqual({ match: 'eligible' })
  })

  it('rejects the same amount in a different currency', () => {
    const postings = [makePosting({ posting_id: 'euros', amount: 100, currency: 'EUR' })]

    expect(eligibilityOf(postings)).toEqual({ euros: 'amount-mismatch' })
  })

  // A cent of rounding slack, and no more — a genuinely mismatched pair should
  // read as a mismatch rather than silently link.
  it('allows under a cent of drift and no more', () => {
    const postings = [
      makePosting({ posting_id: 'near', amount: 100.009 }),
      makePosting({ posting_id: 'off', amount: 100.01 }),
    ]

    expect(eligibilityOf(postings)).toEqual({ near: 'eligible', off: 'amount-mismatch' })
  })

  it('refuses a transaction already linked to something else', () => {
    const postings = [makePosting({ posting_id: 'taken', amount: 100, is_linked_transfer: true })]
    const hint = pickHintByPostingId(postings, SOURCE)?.get('taken')

    expect(hint?.eligibility).toBe('already-linked')
    expect(hint?.tooltip).toBe('Already linked to another transaction')
  })

  it('refuses a split leg, which has no whole transaction to link', () => {
    const postings = [makePosting({ posting_id: 'p9:split:0', amount: 100 })]

    expect(eligibilityOf(postings)).toEqual({ 'p9:split:0': 'already-split' })
  })

  it('says what amount a mismatched row would have needed', () => {
    const postings = [makePosting({ posting_id: 'off', amount: 5 })]

    expect(pickHintByPostingId(postings, SOURCE)?.get('off')?.tooltip).toContain('100')
  })

  it('offers no tooltip on an eligible row', () => {
    const postings = [makePosting({ posting_id: 'match', amount: 100 })]

    expect(pickHintByPostingId(postings, SOURCE)?.get('match')?.tooltip).toBeUndefined()
  })
})
