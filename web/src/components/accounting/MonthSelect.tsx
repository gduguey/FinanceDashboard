import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { formatMonthLong } from '@/lib/format'
import type { Posting } from '@/types/accounting'

// Every month that actually has a transaction, newest first — picking a
// month with nothing in it is a dead end, so there's no reason to offer one.
export function availableMonths(postings: Posting[]): string[] {
  const months = new Set(postings.map((posting) => posting.posted_at.slice(0, 7)))
  return [...months].sort().reverse()
}

export function MonthSelect({
  value,
  onChange,
  months,
  className,
}: {
  value: string
  onChange: (value: string) => void
  months: string[]
  className?: string
}) {
  // The current selection might not have any transactions yet (e.g. this
  // month, so far) — keep it in the list anyway rather than silently
  // reverting to whatever the newest populated month is.
  const options = months.includes(value) ? months : [value, ...months]
  const items = Object.fromEntries(options.map((month) => [month, formatMonthLong(month)]))

  return (
    <Select value={value} onValueChange={(next) => next && onChange(next)}>
      <SelectTrigger size="sm" className={className ?? 'w-40'}>
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        {options.map((month) => (
          <SelectItem key={month} value={month}>
            {formatMonthLong(month)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
