import { describe, expect, it } from 'vitest'
import { formatCurrency, formatCurrencyCompact, formatMonthLong, formatUsd, signColor } from '@/lib/format'

describe('formatUsd', () => {
  it('formats a positive amount as USD', () => {
    expect(formatUsd(1234.5)).toBe('$1,234.50')
  })

  it('formats compactly when requested', () => {
    expect(formatUsd(1_234_567, true)).toBe('$1,234,567')
  })
})

describe('formatCurrency', () => {
  it('formats USD', () => {
    expect(formatCurrency(1234.5, 'USD')).toBe('$1,234.50')
  })

  it('formats EUR', () => {
    expect(formatCurrency(1234.5, 'EUR')).toBe('€1,234.50')
  })

  it('falls back to USD for an unsupported currency code', () => {
    expect(formatCurrency(1234.5, 'GBP')).toBe('$1,234.50')
  })
})

describe('formatCurrencyCompact', () => {
  it('formats USD with K/M/B notation and no decimals', () => {
    expect(formatCurrencyCompact(1_234_567, 'USD')).toBe('$1M')
  })

  it('formats EUR with K/M/B notation and no decimals', () => {
    expect(formatCurrencyCompact(1_234_567, 'EUR')).toBe('€1M')
  })

  // An unsupported code falls back to `usdCompact` (decimal-free, but not
  // K/M/B notation) rather than the K/M/B-notation USD formatter — the two
  // "compact" formatters in format.ts serve different call sites and aren't
  // interchangeable; this pins down the fallback's actual current behavior.
  it('falls back to plain decimal-free USD (not K/M/B notation) for an unsupported currency code', () => {
    expect(formatCurrencyCompact(1_234_567, 'GBP')).toBe('$1,234,567')
  })
})

describe('formatMonthLong', () => {
  it('formats a YYYY-MM string as a full month name and year', () => {
    expect(formatMonthLong('2026-03')).toBe('March 2026')
  })
})

describe('signColor', () => {
  it('paints a gain green', () => {
    expect(signColor(1)).toBe('text-emerald-600')
  })

  it('paints a loss red', () => {
    expect(signColor(-1)).toBe('text-rose-600')
  })

  // A3d: `value >= 0` painted exactly zero green, so a balance of £0.00 and
  // a month with no change both read as a gain.
  it('paints exactly zero neither green nor red', () => {
    expect(signColor(0)).toBe('text-muted-foreground')
  })

  // Several call sites negate their argument because growth is the bad
  // direction there (`signColor(-liabilities)`), and negating zero gives
  // `-0`, which must stay neutral rather than falling through to red.
  it('treats negative zero as zero', () => {
    expect(signColor(-0)).toBe('text-muted-foreground')
  })

  it('has no colour for a missing or unusable value', () => {
    expect(signColor(null)).toBe('text-muted-foreground')
    expect(signColor(undefined)).toBe('text-muted-foreground')
    expect(signColor(Number.NaN)).toBe('text-muted-foreground')
  })
})
