import { describe, expect, it } from 'vitest'
import { routePathFromFile } from '@/lib/routing'

describe('routePathFromFile', () => {
  it('maps a top-level index file to the root path', () => {
    expect(routePathFromFile('./routes/index.tsx')).toBe('/')
  })

  it('maps a flat file to its own path', () => {
    expect(routePathFromFile('./routes/onboarding.tsx')).toBe('/onboarding')
    expect(routePathFromFile('./routes/net-worth.tsx')).toBe('/net-worth')
  })

  it("maps a nested index file to its parent directory's own path", () => {
    expect(routePathFromFile('./routes/investments/index.tsx')).toBe('/investments')
  })

  it('maps a nested file to a nested path', () => {
    expect(routePathFromFile('./routes/investments/allocation.tsx')).toBe('/investments/allocation')
  })
})
