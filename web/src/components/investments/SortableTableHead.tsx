import { ArrowDown, ArrowUp } from 'lucide-react'
import { TableHead } from '@/components/ui/table'

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
    <TableHead className={align === 'right' ? 'text-right' : ''}>
      <button onClick={onClick} className="inline-flex items-center gap-1 hover:text-foreground">
        {children}
        {active && (desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </button>
    </TableHead>
  )
}
