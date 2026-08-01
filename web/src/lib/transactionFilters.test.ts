import { describe, expect, it } from 'vitest'
import { queryString } from '@/lib/accountingApi'
import { FILTER_ALL as ALL } from '@/lib/filters'
import {
  ALL_MONTHS,
  activeFilterCount,
  DATE_MODE_RANGE,
  defaultFilterState,
  type FilterState,
  normalizeFilterState,
  toPostingFilters,
  withSearch,
} from '@/lib/transactionFilters'

function withFilters(overrides: Partial<FilterState>): FilterState {
  return { ...withSearch(defaultFilterState(), ''), ...overrides }
}

function asQuery(overrides: Partial<FilterState>, onlyUncategorized = false) {
  return toPostingFilters(withFilters(overrides), { onlyUncategorized })
}

describe('toPostingFilters', () => {
  it('restricts nothing when nothing is picked', () => {
    const query = asQuery({})

    expect(query.account).toBeNull()
    expect(query.month).toBeNull()
    expect(query.start).toBeNull()
    expect(query.end).toBeNull()
    expect(query.income_expense).toBeNull()
    expect(query.categorized).toBeNull()
    expect(query.categories).toEqual([])
    expect(query.needs_categorizing).toBe(false)
  })

  it('passes each multi-select through with its own exclude flag', () => {
    const query = asQuery({
      categoryFilter: ['expense:food', 'expense:rent'],
      categoryExclude: true,
      tagFilter: ['tag:trip'],
    })

    expect(query.categories).toEqual(['expense:food', 'expense:rent'])
    expect(query.categories_exclude).toBe(true)
    expect(query.tags).toEqual(['tag:trip'])
    expect(query.tags_exclude).toBe(false)
  })

  // The bar offers a month picker *or* a range, never both — so whichever one
  // is not the live control must not reach the server, or a stale bound left
  // in the other would narrow a filter nobody set.
  it('sends only the date control the mode has selected', () => {
    const byMonth = asQuery({ month: '2026-03', startDate: '2020-01-01', endDate: '2020-12-31' })
    expect(byMonth.month).toBe('2026-03')
    expect(byMonth.start).toBeNull()
    expect(byMonth.end).toBeNull()

    const byRange = asQuery({ dateMode: DATE_MODE_RANGE, month: '2026-03', startDate: '2026-01-01' })
    expect(byRange.month).toBeNull()
    expect(byRange.start).toBe('2026-01-01')
    expect(byRange.end).toBeNull()
  })

  it('turns the "all" sentinels into no restriction', () => {
    const query = asQuery({ accountFilter: ALL, month: ALL_MONTHS, incomeExpenseFilter: ALL, categorizedFilter: ALL })

    expect(query.account).toBeNull()
    expect(query.month).toBeNull()
    expect(query.income_expense).toBeNull()
    expect(query.categorized).toBeNull()
  })

  it('carries the "needs categorizing" tab as its own predicate', () => {
    expect(asQuery({}, true).needs_categorizing).toBe(true)
  })

  // `localStorage` can hold a value written by any past version of the app.
  // Dropping an unrecognized one is the safe direction: sending it would be a
  // predicate the server either rejects or silently matches nothing with.
  it('drops a persisted value the server no longer knows', () => {
    const query = asQuery({
      transferFlagFilter: ['rule', 'a-flag-that-was-removed'],
      incomeExpenseFilter: 'something-else',
    })

    expect(query.transfer_flags).toEqual(['rule'])
    expect(query.income_expense).toBeNull()
  })
})

describe('the filter as it reaches the wire', () => {
  // The regression this block exists for. `URLSearchParams.set(key, String([...]))`
  // writes `categories=a,b`; FastAPI parses that as the single category
  // `"a,b"`, matches nothing, and answers 200 with an empty page. No type is
  // violated and no error is raised — the filter just selects the wrong set.
  it('repeats a key per value rather than comma-joining a list', () => {
    const encoded = queryString({ ...asQuery({ categoryFilter: ['expense:food', 'expense:rent'] }) })

    expect(encoded).toContain('categories=expense%3Afood&categories=expense%3Arent')
    expect(encoded).not.toContain('%2C')
  })

  it('reads back as the list it started as', () => {
    const query = asQuery({ tagFilter: ['tag:a', 'tag:b'], pendingFilter: ['ai'] })
    const params = new URLSearchParams(queryString({ ...query }).slice(1))

    expect(params.getAll('tags')).toEqual(['tag:a', 'tag:b'])
    expect(params.getAll('pending')).toEqual(['ai'])
  })

  it('omits an empty multi-select and every null, which the server reads as no restriction', () => {
    const params = new URLSearchParams(queryString({ ...asQuery({}) }).slice(1))

    expect(params.getAll('categories')).toEqual([])
    expect(params.has('account')).toBe(false)
    expect(params.has('month')).toBe(false)
  })

  it('keeps a term the search box would otherwise smuggle wildcards through', () => {
    const params = new URLSearchParams(queryString({ ...asQuery({}), search: '50% & more' }).slice(1))

    expect(params.get('search')).toBe('50% & more')
  })
})

describe('normalizeFilterState', () => {
  it('fills in a completely absent state', () => {
    expect(normalizeFilterState(null)).toEqual(defaultFilterState())
    expect(normalizeFilterState(undefined)).toEqual(defaultFilterState())
  })

  // A state persisted before these went single-to-multi holds a bare string,
  // and a string has `.includes` too — so a stale value used to substring-match
  // instead of failing.
  it('coerces a stale single-select value into an array', () => {
    const stale = { categoryFilter: 'expense:food', tagFilter: 'tag:trip' } as unknown as Partial<FilterState>
    const normalized = normalizeFilterState(stale)

    expect(normalized.categoryFilter).toEqual([])
    expect(normalized.tagFilter).toEqual([])
  })

  it('keeps the values it recognizes', () => {
    const normalized = normalizeFilterState({ categoryFilter: ['expense:food'], accountFilter: 'checking' })

    expect(normalized.categoryFilter).toEqual(['expense:food'])
    expect(normalized.accountFilter).toBe('checking')
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
