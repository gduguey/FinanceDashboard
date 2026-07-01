const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
const usdCompact = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

export function formatUsd(value: number, compact = false): string {
  return compact ? usdCompact.format(value) : usd.format(value)
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
