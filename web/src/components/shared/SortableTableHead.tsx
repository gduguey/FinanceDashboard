import { ArrowDown, ArrowUp } from 'lucide-react'
import { TableHead } from '@/components/ui/table'
import { cn } from '@/lib/utils'

export function SortableTableHead({
  active,
  desc,
  onClick,
  align,
  children,
}: {
  active: boolean
  desc: boolean
  onClick: () => void
  align?: 'right'
  children: React.ReactNode
}) {
  return (
    // `overflow-hidden` only bites in the fixed-width tables (`LinkedTransactionsTable`,
    // `ExcludedTransactionsTable`) — there the column's width is a hard
    // percentage, so the label plus the sort arrow can exceed it and, with
    // the default `overflow: visible` on `<th>`, bleed straight into the
    // next column's header text instead of just being clipped. Inert
    // everywhere else: an auto-layout `<th>` never shrinks below its
    // content's natural width, so nothing here actually gets cut off.
    <TableHead className={cn('overflow-hidden', align === 'right' && 'text-right')}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'inline-flex w-full items-center gap-1 hover:text-foreground',
          align === 'right' && 'justify-end',
        )}
      >
        <span className="truncate">{children}</span>
        {active && (desc ? <ArrowDown className="size-3 shrink-0" /> : <ArrowUp className="size-3 shrink-0" />)}
      </button>
    </TableHead>
  )
}
