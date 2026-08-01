import { afterEach, describe, expect, it, vi } from 'vitest'
import { accountingApi } from '@/lib/accountingApi'
import { PAGE_LIMIT_MAX } from '@/lib/paging'

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

/** Every `transaction_ids` list the stub was handed, in order. */
function captureLegRequests(): { bodies: string[][]; fetch: ReturnType<typeof vi.fn> } {
  const bodies: string[][] = []
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    const ids = (JSON.parse(String(init?.body)) as { transaction_ids: string[] }).transaction_ids
    bodies.push(ids)
    return jsonResponse(Object.fromEntries(ids.map((id) => [id, { transaction_id: id }])))
  })
  vi.stubGlobal('fetch', fetch)
  return { bodies, fetch }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

// `TransactionLegsRequest` declares `max_length=PAGE_LIMIT_MAX`, so a longer
// list is a 422. The Rules page's list is every transfer link plus every rule
// exclusion the user has, which nothing bounds — and the failure was silent
// in the worst direction: the request rejects, the hook holds no data, and
// the three tabs render fallback values rather than an error.
describe('transactionLegs', () => {
  it('sends one request when the list fits under the server’s cap', async () => {
    const { bodies } = captureLegRequests()

    await accountingApi.transactionLegs(['t1', 't2'])

    expect(bodies).toEqual([['t1', 't2']])
  })

  it('chunks a list longer than the cap rather than sending a request the server rejects', async () => {
    const { bodies } = captureLegRequests()
    const ids = Array.from({ length: PAGE_LIMIT_MAX + 3 }, (_, index) => `t${index}`)

    const legs = await accountingApi.transactionLegs(ids)

    expect(bodies.map((chunk) => chunk.length)).toEqual([PAGE_LIMIT_MAX, 3])
    expect(bodies.every((chunk) => chunk.length <= PAGE_LIMIT_MAX)).toBe(true)
    // Merged, not last-wins: every id the caller named is answered for.
    expect(Object.keys(legs)).toHaveLength(ids.length)
  })

  it('makes no request for an empty list', async () => {
    const { fetch } = captureLegRequests()

    await expect(accountingApi.transactionLegs([])).resolves.toEqual({})
    expect(fetch).not.toHaveBeenCalled()
  })
})
