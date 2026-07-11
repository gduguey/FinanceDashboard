import { describe, expect, it } from 'vitest'
import { FILTER_ALL, matchesFilter } from '@/components/shared/FilterSelect'

describe('matchesFilter', () => {
  it('matches everything when no filter value is set', () => {
    expect(matchesFilter(true, undefined, false)).toBe(true)
    expect(matchesFilter(false, undefined, true)).toBe(true)
  })

  it('matches everything when the filter value is the "all" sentinel', () => {
    expect(matchesFilter(false, FILTER_ALL, true)).toBe(true)
  })

  it("returns the row's own match when a filter is set without exclude", () => {
    expect(matchesFilter(true, 'checking', false)).toBe(true)
    expect(matchesFilter(false, 'checking', false)).toBe(false)
  })

  it("inverts the row's match when a filter is set with exclude", () => {
    expect(matchesFilter(true, 'checking', true)).toBe(false)
    expect(matchesFilter(false, 'checking', true)).toBe(true)
  })
})
