import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, parseBody } from '@/lib/api'

describe('parseBody', () => {
  // Both request wrappers used to call `response.json()` unconditionally.
  // Once every delete answered 204, that threw "Unexpected end of JSON input"
  // on a request the server had already carried out — a delete that worked and
  // reported failure. This is the regression test for that.
  it('returns undefined for a 204 instead of parsing an absent body', async () => {
    const response = new Response(null, { status: 204 })
    await expect(parseBody(response)).resolves.toBeUndefined()
  })

  it('parses the body of a 200', async () => {
    const response = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
    await expect(parseBody(response)).resolves.toEqual({ ok: true })
  })
})

// C4a: `GET /lots` used to answer with all three collections at once, in one
// unbounded response. Each is its own paged read now, and `api.lots()` walks
// every page of every one — the property under test, because a lots table
// that stopped at the server's page size would drop tax lots from a screen
// and from an export with nothing to show that it had.
describe('api.lots', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function pagedFetch(collections: Record<string, unknown[]>, pageSize: number) {
    return vi.fn((url: string) => {
      const { pathname, searchParams } = new URL(url, 'http://localhost')
      const items = collections[pathname.split('/').pop() as string]
      const offset = Number(searchParams.get('offset'))
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: items.slice(offset, offset + pageSize),
            window_unit: 'lot',
            total: items.length,
            // Clamped below what the client asked for, which is what makes
            // the stride the response's rather than the request's.
            limit: pageSize,
            offset,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    })
  }

  it('walks every page of all three collections rather than returning the first', async () => {
    const collections = {
      open: [{ lot_id: 'o1' }, { lot_id: 'o2' }, { lot_id: 'o3' }],
      closed: [{ lot_id: 'c1' }, { lot_id: 'c2' }],
      symbols: [{ symbol: 'VOO' }],
    }
    const fetchMock = pagedFetch(collections, 2)
    vi.stubGlobal('fetch', fetchMock)

    const lots = await api.lots()

    expect(lots.open_lots).toHaveLength(3)
    expect(lots.closed_lots).toHaveLength(2)
    expect(lots.symbol_rollup).toHaveLength(1)
    // Sorted, because the three walks are concurrent and their interleaving
    // is the scheduler's business. What matters is that `open` was asked for
    // a second window and the other two were not asked for one they have no
    // rows in.
    expect(fetchMock.mock.calls.map(([url]) => url as string).sort()).toEqual([
      '/api/v1/trades/lots/closed?limit=5000&offset=0',
      '/api/v1/trades/lots/open?limit=5000&offset=0',
      '/api/v1/trades/lots/open?limit=5000&offset=2',
      '/api/v1/trades/lots/symbols?limit=5000&offset=0',
    ])
  })

  it('passes as_of through to every collection', async () => {
    const fetchMock = pagedFetch({ open: [], closed: [], symbols: [] }, 5000)
    vi.stubGlobal('fetch', fetchMock)

    await api.lots('2026-01-03')

    for (const [url] of fetchMock.mock.calls) {
      expect(url as string).toContain('as_of=2026-01-03')
    }
  })
})
