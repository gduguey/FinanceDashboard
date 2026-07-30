import { describe, expect, it } from 'vitest'
import { FILTER_ALL as ALL } from '@/lib/filters'
import {
  ALL_MONTHS,
  activeFilterCount,
  DATE_MODE_RANGE,
  defaultFilterState,
  type FilterContext,
  type FilterState,
  filterPostings,
  NO_SUBCATEGORY,
  normalizeFilterState,
  transferFlagsForPosting,
  UNCATEGORIZED,
} from '@/lib/transactionFilters'
import type { Posting } from '@/types/accounting'

function makePosting(overrides: Partial<Posting> & Pick<Posting, 'posting_id'>): Posting {
  return {
    transaction_id: `t:${overrides.posting_id}`,
    account_id: 'checking',
    posted_at: '2026-01-15T00:00:00',
    amount: -25,
    currency: 'USD',
    description: 'Corner Store',
    category_id: null,
    subcategory_id: null,
    tag_ids: [],
    pending_source: null,
    pending_selected: true,
    is_linked_transfer: false,
    ...overrides,
  } as Posting
}

function context(overrides: Partial<FilterContext> = {}): FilterContext {
  return {
    onlyUncategorized: false,
    withSubcategories: new Set(),
    realIncomeExpensePostingIds: new Set(['p1', 'p2', 'p3']),
    excludedTransactionIds: new Set(),
    ...overrides,
  }
}

function withFilters(overrides: Partial<FilterState>): FilterState {
  return { ...defaultFilterState(), ...overrides }
}

function keptIds(postings: Posting[], filters: Partial<FilterState>, ctx = context()): string[] {
  return filterPostings(postings, withFilters(filters), ctx).map((posting) => posting.posting_id)
}

describe('defaultFilterState', () => {
  it('narrows nothing', () => {
    const postings = [makePosting({ posting_id: 'p1' }), makePosting({ posting_id: 'p2' })]

    expect(keptIds(postings, {})).toEqual(['p1', 'p2'])
    expect(activeFilterCount(defaultFilterState())).toBe(0)
  })
})

describe('normalizeFilterState', () => {
  it('falls back to the defaults for absent state', () => {
    expect(normalizeFilterState(null)).toEqual(defaultFilterState())
  })

  // A filter bar persisted before these went multi-select left a plain string
  // behind, and `'food'.includes('foo')` is true — so a stale value used to
  // substring-match instead of failing.
  it('coerces a single-select value left over from an older install', () => {
    const stale = { categoryFilter: 'food', tagFilter: 'holiday' } as unknown as Partial<FilterState>

    const normalized = normalizeFilterState(stale)

    expect(normalized.categoryFilter).toEqual([])
    expect(normalized.tagFilter).toEqual([])
  })

  it('fills in a key an older persisted state never had', () => {
    const normalized = normalizeFilterState({ search: 'coffee' })

    expect(normalized.transferFlagFilter).toEqual([])
    expect(normalized.incomeExpenseFilter).toBe(ALL)
    expect(normalized.search).toBe('coffee')
  })

  it('keeps a value that is already valid', () => {
    expect(normalizeFilterState({ categoryFilter: ['food', 'rent'] }).categoryFilter).toEqual(['food', 'rent'])
  })
})

describe('filterPostings', () => {
  it('never shows a placeholder counterparty leg', () => {
    const postings = [
      makePosting({ posting_id: 'p1' }),
      makePosting({ posting_id: 'ghost', account_id: 'uncategorized:expense' }),
    ]

    expect(keptIds(postings, {})).toEqual(['p1'])
  })

  it('matches the search term case-insensitively against the description', () => {
    const postings = [
      makePosting({ posting_id: 'p1', description: 'Corner Store' }),
      makePosting({ posting_id: 'p2', description: 'Rent' }),
    ]

    expect(keptIds(postings, { search: 'corner' })).toEqual(['p1'])
  })

  it("shows every account but the picked one in the account filter's exclude mode", () => {
    const postings = [
      makePosting({ posting_id: 'p1', account_id: 'checking' }),
      makePosting({ posting_id: 'p2', account_id: 'savings' }),
    ]

    expect(keptIds(postings, { accountFilter: 'checking', accountExclude: true })).toEqual(['p2'])
    expect(keptIds(postings, { accountFilter: 'checking', accountExclude: false })).toEqual(['p1'])
  })

  it('treats a posting with no category as the "Uncategorized" option', () => {
    const postings = [
      makePosting({ posting_id: 'p1', category_id: null }),
      makePosting({ posting_id: 'p2', category_id: 'food' }),
    ]

    expect(keptIds(postings, { categoryFilter: [UNCATEGORIZED] })).toEqual(['p1'])
  })

  it('treats a posting with no subcategory as the "None" option', () => {
    const postings = [
      makePosting({ posting_id: 'p1', subcategory_id: null }),
      makePosting({ posting_id: 'p2', subcategory_id: 'groceries' }),
    ]

    expect(keptIds(postings, { subcategoryFilter: [NO_SUBCATEGORY] })).toEqual(['p1'])
  })

  it('keeps a posting carrying any one of the picked tags', () => {
    const postings = [
      makePosting({ posting_id: 'p1', tag_ids: ['holiday'] }),
      makePosting({ posting_id: 'p2', tag_ids: ['work', 'holiday'] }),
      makePosting({ posting_id: 'p3', tag_ids: [] }),
    ]

    expect(keptIds(postings, { tagFilter: ['holiday'] })).toEqual(['p1', 'p2'])
    expect(keptIds(postings, { tagFilter: ['holiday'], tagExclude: true })).toEqual(['p3'])
  })

  it('bounds by month in month mode', () => {
    const postings = [
      makePosting({ posting_id: 'p1', posted_at: '2026-01-15T00:00:00' }),
      makePosting({ posting_id: 'p2', posted_at: '2026-02-01T00:00:00' }),
    ]

    expect(keptIds(postings, { month: '2026-01' })).toEqual(['p1'])
    expect(keptIds(postings, { month: ALL_MONTHS })).toEqual(['p1', 'p2'])
  })

  it('bounds inclusively at both ends in range mode', () => {
    const postings = [
      makePosting({ posting_id: 'p1', posted_at: '2026-01-15T00:00:00' }),
      makePosting({ posting_id: 'p2', posted_at: '2026-02-01T00:00:00' }),
      makePosting({ posting_id: 'p3', posted_at: '2026-03-01T00:00:00' }),
    ]

    expect(keptIds(postings, { dateMode: DATE_MODE_RANGE, startDate: '2026-01-15', endDate: '2026-02-01' })).toEqual([
      'p1',
      'p2',
    ])
  })

  // The range bounds are independent: setting only one leaves the other open.
  it('leaves the other end open when only one range bound is set', () => {
    const postings = [
      makePosting({ posting_id: 'p1', posted_at: '2026-01-15T00:00:00' }),
      makePosting({ posting_id: 'p2', posted_at: '2026-02-01T00:00:00' }),
    ]

    expect(keptIds(postings, { dateMode: DATE_MODE_RANGE, endDate: '2026-01-31' })).toEqual(['p1'])
    expect(keptIds(postings, { dateMode: DATE_MODE_RANGE, startDate: '2026-01-31' })).toEqual(['p2'])
  })

  it('treats a posting with no suggestion as the "Confirmed" option', () => {
    const postings = [
      makePosting({ posting_id: 'p1', pending_source: null }),
      makePosting({ posting_id: 'p2', pending_source: 'ai' }),
      makePosting({ posting_id: 'p3', pending_source: 'pattern' }),
    ]

    expect(keptIds(postings, { pendingFilter: ['ai'] })).toEqual(['p2'])
    expect(keptIds(postings, { pendingFilter: ['ai', 'pattern'], pendingExclude: true })).toEqual(['p1'])
  })

  it('restricts income and expense to real income/expense legs', () => {
    const postings = [
      makePosting({ posting_id: 'p1', amount: 100 }),
      makePosting({ posting_id: 'p2', amount: -100 }),
      makePosting({ posting_id: 'transfer', amount: 100 }),
    ]
    const ctx = context({ realIncomeExpensePostingIds: new Set(['p1', 'p2']) })

    expect(keptIds(postings, { incomeExpenseFilter: 'income' }, ctx)).toEqual(['p1'])
    expect(keptIds(postings, { incomeExpenseFilter: 'expense' }, ctx)).toEqual(['p2'])
    expect(keptIds(postings, { incomeExpenseFilter: ALL }, ctx)).toEqual(['p1', 'p2', 'transfer'])
  })

  // Zero is income, matching the resolved sign convention: money arriving is
  // non-negative.
  it('counts a zero amount as income', () => {
    const postings = [makePosting({ posting_id: 'p1', amount: 0 })]

    expect(keptIds(postings, { incomeExpenseFilter: 'income' })).toEqual(['p1'])
    expect(keptIds(postings, { incomeExpenseFilter: 'expense' })).toEqual([])
  })

  it('splits on whether a category is set at all', () => {
    const postings = [
      makePosting({ posting_id: 'p1', category_id: 'food' }),
      makePosting({ posting_id: 'p2', category_id: null }),
    ]

    expect(keptIds(postings, { categorizedFilter: 'categorized' })).toEqual(['p1'])
    expect(keptIds(postings, { categorizedFilter: 'uncategorized' })).toEqual(['p2'])
  })

  it('applies every set filter together', () => {
    const postings = [
      makePosting({ posting_id: 'p1', description: 'Rent', category_id: 'housing', account_id: 'checking' }),
      makePosting({ posting_id: 'p2', description: 'Rent', category_id: 'housing', account_id: 'savings' }),
      makePosting({ posting_id: 'p3', description: 'Coffee', category_id: 'housing', account_id: 'checking' }),
    ]

    expect(keptIds(postings, { search: 'rent', accountFilter: 'checking', categoryFilter: ['housing'] })).toEqual([
      'p1',
    ])
  })

  describe('the needs-categorizing tab', () => {
    const ctx = context({ onlyUncategorized: true, withSubcategories: new Set(['food']) })

    it('keeps a posting with no category', () => {
      expect(keptIds([makePosting({ posting_id: 'p1', category_id: null })], {}, ctx)).toEqual(['p1'])
    })

    it('keeps a posting whose category has subcategories but no subcategory picked', () => {
      expect(keptIds([makePosting({ posting_id: 'p1', category_id: 'food' })], {}, ctx)).toEqual(['p1'])
    })

    it('drops a posting whose category has no subcategories to pick', () => {
      expect(keptIds([makePosting({ posting_id: 'p1', category_id: 'rent' })], {}, ctx)).toEqual([])
    })

    it('keeps a categorized posting whose suggestion is still unconfirmed', () => {
      const posting = makePosting({ posting_id: 'p1', category_id: 'rent', pending_source: 'ai' })

      expect(keptIds([posting], {}, ctx)).toEqual(['p1'])
    })

    it('drops an internal transfer, which has no category to pick', () => {
      const ctxWithTransfer = context({ ...ctx, realIncomeExpensePostingIds: new Set() })

      expect(keptIds([makePosting({ posting_id: 'p1', category_id: null })], {}, ctxWithTransfer)).toEqual([])
    })
  })
})

describe('transferFlagsForPosting', () => {
  const none = new Set<string>()

  it('flags a posting nothing has touched as a non-transfer', () => {
    expect(transferFlagsForPosting(makePosting({ posting_id: 'p1' }), none)).toEqual(['none'])
  })

  it('flags a rule-repointed posting', () => {
    const posting = makePosting({ posting_id: 'p1', resolved_by_transfer_rule_id: 'r1' })

    expect(transferFlagsForPosting(posting, none)).toEqual(['rule'])
  })

  it('flags a rule-found link', () => {
    const posting = makePosting({ posting_id: 'p1', is_linked_transfer: true, transfer_link_source: 'rule' })

    expect(transferFlagsForPosting(posting, none)).toEqual(['rule'])
  })

  it('flags a manual link and a manual account repoint alike', () => {
    const linked = makePosting({ posting_id: 'p1', is_linked_transfer: true, transfer_link_source: 'manual' })
    const repointed = makePosting({ posting_id: 'p2', manual_transfer_override_posting_id: 'p9' })

    expect(transferFlagsForPosting(linked, none)).toEqual(['manual'])
    expect(transferFlagsForPosting(repointed, none)).toEqual(['manual'])
  })

  // "Excluded from a rule" is history, not a classification, so it never costs
  // an otherwise-untouched posting its place in the "Non transfer" filter.
  it('keeps the non-transfer flag on an excluded posting nothing else flags', () => {
    const posting = makePosting({ posting_id: 'p1', transaction_id: 't1' })

    expect(transferFlagsForPosting(posting, new Set(['t1']))).toEqual(['excluded', 'none'])
  })

  it('reports excluded alongside a rule that still flags the posting', () => {
    const posting = makePosting({ posting_id: 'p1', transaction_id: 't1', resolved_by_transfer_rule_id: 'r1' })

    expect(transferFlagsForPosting(posting, new Set(['t1']))).toEqual(['rule', 'excluded'])
  })
})

describe('the transfer-flag filter', () => {
  const postings = [
    makePosting({ posting_id: 'p1', transaction_id: 't1', resolved_by_transfer_rule_id: 'r1' }),
    makePosting({ posting_id: 'p2', transaction_id: 't2', manual_transfer_override_posting_id: 'p9' }),
    makePosting({ posting_id: 'p3', transaction_id: 't3' }),
  ]
  const ctx = context({ excludedTransactionIds: new Set(['t3']) })

  it('keeps only the postings carrying a picked flag', () => {
    expect(keptIds(postings, { transferFlagFilter: ['rule'] }, ctx)).toEqual(['p1'])
    expect(keptIds(postings, { transferFlagFilter: ['manual'] }, ctx)).toEqual(['p2'])
    expect(keptIds(postings, { transferFlagFilter: ['none'] }, ctx)).toEqual(['p3'])
  })

  it('reads "excluded" off the rules rather than off the posting', () => {
    expect(keptIds(postings, { transferFlagFilter: ['excluded'] }, ctx)).toEqual(['p3'])
  })

  it('inverts the whole set in exclude mode', () => {
    expect(keptIds(postings, { transferFlagFilter: ['rule', 'manual'], transferFlagExclude: true }, ctx)).toEqual([
      'p3',
    ])
  })
})

describe('activeFilterCount', () => {
  it('counts a single-select once and a multi-select once per value', () => {
    const filters = withFilters({ accountFilter: 'checking', categoryFilter: ['food', 'rent'] })

    expect(activeFilterCount(filters)).toBe(3)
  })

  it('counts the date row once however many bounds are set', () => {
    const both = withFilters({ dateMode: DATE_MODE_RANGE, startDate: '2026-01-01', endDate: '2026-02-01' })
    const one = withFilters({ dateMode: DATE_MODE_RANGE, startDate: '2026-01-01' })

    expect(activeFilterCount(both)).toBe(1)
    expect(activeFilterCount(one)).toBe(1)
  })

  it('does not count an unbounded month picker', () => {
    expect(activeFilterCount(withFilters({ month: ALL_MONTHS }))).toBe(0)
  })
})
