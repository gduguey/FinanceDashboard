import { describe, expect, it } from 'vitest'
import {
  categoriesWithSubcategories,
  needsCategorizing,
  splitOriginalId,
} from '@/components/accounting/transactionCategorization'
import type { Category, Posting } from '@/types/accounting'

function makeCategory(overrides: Partial<Category> & Pick<Category, 'category_id'>): Category {
  return { name: overrides.category_id, classification: 'expense', color: '#000000', ...overrides }
}

function makePosting(overrides: Partial<Posting> & Pick<Posting, 'posting_id'>): Posting {
  return {
    transaction_id: overrides.posting_id,
    account_id: 'checking',
    posted_at: '2026-01-01T00:00:00',
    amount: 100,
    currency: 'USD',
    description: '',
    pending_selected: true,
    ...overrides,
  }
}

describe('splitOriginalId', () => {
  it('extracts the original posting id from a split leg id', () => {
    expect(splitOriginalId('abc123:split:0')).toBe('abc123')
    expect(splitOriginalId('abc123:split:1')).toBe('abc123')
  })

  it('is null for a posting id that was never split', () => {
    expect(splitOriginalId('abc123')).toBeNull()
  })
})

describe('categoriesWithSubcategories', () => {
  it('is empty when no category has a parent', () => {
    const categories = { food: makeCategory({ category_id: 'food' }) }
    expect(categoriesWithSubcategories(categories)).toEqual(new Set())
  })

  it('collects every category id that has at least one subcategory', () => {
    const categories = {
      food: makeCategory({ category_id: 'food' }),
      groceries: makeCategory({ category_id: 'groceries', parent_category_id: 'food' }),
      restaurants: makeCategory({ category_id: 'restaurants', parent_category_id: 'food' }),
    }
    expect(categoriesWithSubcategories(categories)).toEqual(new Set(['food']))
  })
})

describe('needsCategorizing', () => {
  const withSubcategories = new Set(['food'])

  it('never needs categorizing when it is not a real income/expense leg', () => {
    const posting = makePosting({ posting_id: 'p1', category_id: null })
    expect(needsCategorizing(posting, withSubcategories, false)).toBe(false)
  })

  it('needs categorizing when it has no category yet', () => {
    const posting = makePosting({ posting_id: 'p1', category_id: null })
    expect(needsCategorizing(posting, withSubcategories, true)).toBe(true)
  })

  it('needs categorizing when its category has subcategories but none is picked', () => {
    const posting = makePosting({ posting_id: 'p1', category_id: 'food', subcategory_id: null })
    expect(needsCategorizing(posting, withSubcategories, true)).toBe(true)
  })

  it('is categorized once a subcategory is picked for a category that has them', () => {
    const posting = makePosting({ posting_id: 'p1', category_id: 'food', subcategory_id: 'groceries' })
    expect(needsCategorizing(posting, withSubcategories, true)).toBe(false)
  })

  it('is categorized with just a top-level category when that category has no subcategories', () => {
    const posting = makePosting({ posting_id: 'p1', category_id: 'rent', subcategory_id: null })
    expect(needsCategorizing(posting, withSubcategories, true)).toBe(false)
  })

  it('stays "needs categorizing" while an AI/pattern suggestion is still unconfirmed, even if fully categorized', () => {
    const posting = makePosting({
      posting_id: 'p1',
      category_id: 'food',
      subcategory_id: 'groceries',
      pending_source: 'ai',
    })
    expect(needsCategorizing(posting, withSubcategories, true)).toBe(true)
  })
})
