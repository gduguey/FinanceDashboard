const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
const usdCompact = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

export function formatUsd(value: number, compact = false): string {
  return compact ? usdCompact.format(value) : usd.format(value)
}

const currencyFormatters: Record<string, Intl.NumberFormat> = {
  USD: usd,
  EUR: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'EUR' }),
}

const compactCurrencyFormatters: Record<string, Intl.NumberFormat> = {
  USD: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 0 }),
  EUR: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'EUR', notation: 'compact', maximumFractionDigits: 0 }),
}

// Every amount in this app is stored in one of two currencies (see
// `accounting.models.CurrencyCode`) — this always formats in that native
// currency, never converting; conversion only ever happens server-side,
// where an aggregate is computed into a chosen display currency.
export function formatCurrency(value: number, currency: string): string {
  return (currencyFormatters[currency] ?? usd).format(value)
}

// For chart axis ticks, where "$1,234,567" is both wider than the axis
// gutter and more precision than a tick label needs — "$1.2M" instead,
// with no decimals below a million.
export function formatCurrencyCompact(value: number, currency: string): string {
  return (compactCurrencyFormatters[currency] ?? usdCompact).format(value)
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

export function formatDate(value: string): string {
  // `new Date("2026-06-30")` parses as UTC midnight, which `toLocaleDateString`
  // then renders in the browser's local timezone — shifting it back a day
  // west of UTC. Building the Date from local y/m/d components avoids that.
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function formatMonth(value: string): string {
  const [year, month] = value.split('-')
  return new Date(Number(year), Number(month) - 1, 1).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
  })
}

// e.g. "2026-03" -> "March 2026" — for month pickers, where the full name
// reads better than an axis tick's abbreviated one.
export function formatMonthLong(value: string): string {
  const [year, month] = value.split('-')
  return new Date(Number(year), Number(month) - 1, 1).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
  })
}

export function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const diffMs = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(diffMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

export function signColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'text-muted-foreground'
  return value >= 0 ? 'text-emerald-600' : 'text-rose-600'
}
