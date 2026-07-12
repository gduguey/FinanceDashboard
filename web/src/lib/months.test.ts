import { describe, expect, it } from 'vitest'
import { availableMonths } from '@/lib/months'
import type { Posting } from '@/types/accounting'

function makePosting(postedAt: string): Posting {
  return {
    posting_id: postedAt,
    transaction_id: postedAt,
    account_id: 'checking',
    posted_at: postedAt,
    amount: 100,
    currency: 'USD',
    description: '',
    pending_selected: true,
  }
}

describe('availableMonths', () => {
  it('is empty with no postings', () => {
    expect(availableMonths([])).toEqual([])
  })

  it('collapses postings in the same month to one entry', () => {
    const postings = [makePosting('2026-03-01T00:00:00'), makePosting('2026-03-15T00:00:00')]
    expect(availableMonths(postings)).toEqual(['2026-03'])
  })

  it('orders months newest first', () => {
    const postings = [
      makePosting('2026-01-05T00:00:00'),
      makePosting('2026-03-01T00:00:00'),
      makePosting('2026-02-10T00:00:00'),
    ]
    expect(availableMonths(postings)).toEqual(['2026-03', '2026-02', '2026-01'])
  })
})
