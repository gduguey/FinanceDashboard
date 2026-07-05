import { formatCurrency } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

export interface PieLegendSlice {
  key: string
  name: string
  value: number
  color: string
}

// The persistent right-side companion to a pie/donut chart: the grand total
// up top, then every slice of the ring it's paired with, colored and
// clickable — since this app's pies have no on-slice labels (hover-only
// tooltips), this is the only place a slice's name is visible without
// hovering it.
export function PieChartLegend<T extends PieLegendSlice>({
  total,
  slices,
  displayCurrency,
  showPercent = false,
  onSliceClick,
}: {
  total: number
  slices: T[]
  displayCurrency: CurrencyCode
  showPercent?: boolean
  onSliceClick?: (slice: T) => void
}) {
  const sorted = [...slices].sort((a, b) => b.value - a.value)
  return (
    <div className="flex w-56 shrink-0 flex-col gap-2">
      <div>
        <p className="text-xs text-muted-foreground">Total</p>
        <p className="text-lg font-semibold">{formatCurrency(total, displayCurrency)}</p>
      </div>
      <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto text-sm">
        {sorted.map((slice) => (
          <li key={slice.key}>
            <button
              type="button"
              onClick={() => onSliceClick?.(slice)}
              disabled={!onSliceClick}
              className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-left enabled:hover:bg-muted enabled:cursor-pointer"
            >
              <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: slice.color }} />
              <span className="flex-1 truncate">{slice.name}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {showPercent && total ? `${((slice.value / total) * 100).toFixed(1)}%` : formatCurrency(slice.value, displayCurrency)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
