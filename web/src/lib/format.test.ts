import { describe, expect, it } from 'vitest'
import { formatUsd } from '@/lib/format'

describe('formatUsd', () => {
  it('formats a positive amount as USD', () => {
    expect(formatUsd(1234.5)).toBe('$1,234.50')
  })

  it('formats compactly when requested', () => {
    expect(formatUsd(1_234_567, true)).toBe('$1,234,567')
  })
})
