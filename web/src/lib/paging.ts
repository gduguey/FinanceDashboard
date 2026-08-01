/**
 * Walking a paged collection, for the two API clients that both have to.
 *
 * The server answers every bounded read with one envelope
 * (`http_api.pagination.Page`), so the arithmetic for walking it is the same
 * whichever ledger it came from. This module is that arithmetic, written
 * once. It used to live inside `accountingApi.ts`, which meant the trades
 * client could not reuse it — and the trades ledger export, which had no
 * pagination at all, was the surface that gap was hiding behind.
 */

/**
 * The server's own hard cap (`http_api.pagination.PAGE_LIMIT_MAX`) — the fewest round trips it will allow.
 *
 * Asking for exactly the cap is deliberate: the server clamps anything
 * larger, so this is the largest page that will ever come back, and the
 * loop below never depends on that guess being right.
 */
export const PAGE_LIMIT_MAX = 5000

/**
 * One page of a collection, mirroring the server's `Page` envelope.
 *
 * `total`, `limit` and `offset` all count `window_unit`s, which is not
 * always what `items` counts — `GET /postings` cuts its window by
 * transaction and returns every leg of each. That difference is exactly why
 * `items.length` is never the stride.
 */
export interface Page<T> {
  items: T[]
  window_unit: string
  total: number
  limit: number
  offset: number
}

/**
 * How far a paging loop may advance, given the page size the server applied.
 *
 * Guards the one input that could hang the tab: a stride of zero would make
 * the loop re-request the same offset forever. The server validates
 * `limit >= 1`, so this should be unreachable — which is exactly why it
 * should fail loudly rather than spin.
 */
export function pageStride(appliedLimit: number): number {
  if (!Number.isInteger(appliedLimit) || appliedLimit < 1) {
    throw new Error(`Server returned an unusable page limit: ${appliedLimit}`)
  }
  return appliedLimit
}

/**
 * Read a whole collection by walking its pages, and never return a partial one.
 *
 * A silently truncated ledger is not an option for a money app: an export
 * that stopped at the cap would write a partial backup to a file the user
 * believes is complete, and a transaction list that stopped there would show
 * a balance that is simply wrong. Both are failures the caller cannot see,
 * which is why this loops rather than letting each call site decide.
 *
 * The stride is `page.limit`, the size the server actually applied after
 * clamping — never the size we asked for. If `PAGE_LIMIT_MAX` here is ever
 * above the server's own cap (mid-deploy, say), advancing by the request
 * would step past records the server never sent and truncate the collection
 * silently, which is the exact failure paging exists to avoid.
 *
 * @param fetchPage - Fetches one page at the given window.
 * @param limit - Page size to request. Defaults to the server's cap, the fewest round trips available.
 * @returns Every item in the collection, in page order.
 */
export async function fetchAllPages<T>(
  fetchPage: (window: { limit: number; offset: number }) => Promise<Page<T>>,
  limit: number = PAGE_LIMIT_MAX,
): Promise<T[]> {
  const items: T[] = []
  let offset = 0
  let total = 0
  do {
    const page = await fetchPage({ limit, offset })
    items.push(...page.items)
    total = page.total
    offset += pageStride(page.limit)
  } while (offset < total)
  return items
}
