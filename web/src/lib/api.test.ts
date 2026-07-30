import { describe, expect, it } from 'vitest'
import { parseBody } from '@/lib/api'

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
