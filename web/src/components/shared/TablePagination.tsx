import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

/**
 * Move through a server-paged collection, always saying which slice of it is on screen.
 *
 * The readout is the point, not the buttons. A table that holds one page and
 * says nothing about the rest looks exactly like a table holding everything
 * there is — which is a partial result presented as a complete one, the
 * failure this whole cutover exists to avoid. So the range and the total are
 * always rendered, even on a single-page collection where the arrows are not.
 *
 * `total` and `offset` count whatever the page's own `window_unit` counts —
 * transactions for `GET /postings`, not rows — so the caller names the unit
 * rather than this component assuming one.
 */
export function TablePagination({
  offset,
  limit,
  total,
  unit,
  busy,
  onOffsetChange,
}: {
  offset: number
  limit: number
  total: number
  unit: string
  busy?: boolean
  onOffsetChange: (next: number) => void
}) {
  const first = total === 0 ? 0 : offset + 1
  const last = Math.min(offset + limit, total)
  const hasPrevious = offset > 0
  const hasNext = last < total

  return (
    <div className="flex items-center justify-between gap-3 pt-3 text-xs text-muted-foreground">
      <span aria-live="polite">
        {total === 0
          ? `No ${unit}s`
          : `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()} ${unit}${total === 1 ? '' : 's'}`}
      </span>
      {(hasPrevious || hasNext) && (
        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasPrevious || busy}
            onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          >
            <ChevronLeft className="size-3.5" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext || busy}
            onClick={() => onOffsetChange(offset + limit)}
          >
            Next
            <ChevronRight className="size-3.5" />
          </Button>
        </div>
      )}
    </div>
  )
}
