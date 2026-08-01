import { describe, expect, it, vi } from 'vitest'
import { fetchAllPages, PAGE_LIMIT_MAX, type Page, pageStride } from '@/lib/paging'

function page<T>(items: T[], { total, limit, offset }: { total: number; limit: number; offset: number }): Page<T> {
  return { items, window_unit: 'posting', total, limit, offset }
}

describe('pageStride', () => {
  it('returns the applied limit when the server sent a usable one', () => {
    expect(pageStride(5000)).toBe(5000)
  })

  // A stride of zero would make the paging loop re-request the same offset
  // forever and hang the tab, so this has to throw rather than return 0.
  it.each([0, -1, 1.5, Number.NaN])('throws rather than spinning on a stride of %s', (limit) => {
    expect(() => pageStride(limit)).toThrow(/unusable page limit/)
  })
})

describe('fetchAllPages', () => {
  it('walks every page and concatenates them in order', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(page(['a', 'b'], { total: 5, limit: 2, offset: 0 }))
      .mockResolvedValueOnce(page(['c', 'd'], { total: 5, limit: 2, offset: 2 }))
      .mockResolvedValueOnce(page(['e'], { total: 5, limit: 2, offset: 4 }))

    expect(await fetchAllPages(fetchPage, 2)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(fetchPage.mock.calls.map(([window]) => window)).toEqual([
      { limit: 2, offset: 0 },
      { limit: 2, offset: 2 },
      { limit: 2, offset: 4 },
    ])
  })

  // The failure paging exists to prevent: the server clamped the page to
  // something smaller than we asked for, so advancing by the request rather
  // than the response would step over records it never sent.
  it('advances by the limit the server applied, not the one requested', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(page(['a'], { total: 2, limit: 1, offset: 0 }))
      .mockResolvedValueOnce(page(['b'], { total: 2, limit: 1, offset: 1 }))

    expect(await fetchAllPages(fetchPage, 5000)).toEqual(['a', 'b'])
    expect(fetchPage).toHaveBeenCalledTimes(2)
    expect(fetchPage.mock.calls[1][0]).toEqual({ limit: 5000, offset: 1 })
  })

  it('asks for one page and stops when the collection fits in it', async () => {
    const fetchPage = vi.fn().mockResolvedValue(page(['only'], { total: 1, limit: PAGE_LIMIT_MAX, offset: 0 }))

    expect(await fetchAllPages(fetchPage)).toEqual(['only'])
    expect(fetchPage).toHaveBeenCalledTimes(1)
  })

  // An empty collection still has to terminate after exactly one request —
  // `total: 0` must not be read as "keep going until you find something".
  it('returns nothing for an empty collection without looping', async () => {
    const fetchPage = vi.fn().mockResolvedValue(page([], { total: 0, limit: PAGE_LIMIT_MAX, offset: 0 }))

    expect(await fetchAllPages(fetchPage)).toEqual([])
    expect(fetchPage).toHaveBeenCalledTimes(1)
  })
})
