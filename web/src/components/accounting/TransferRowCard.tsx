import { Truncate } from '@/components/shared/Truncate'
import { formatCurrency, formatDate } from '@/lib/format'
import type { TransferRowInfo } from '@/lib/transferRowInfo'

// A small recognizable summary of one side of a transfer — date, account,
// description, amount — used anywhere a single transaction needs showing
// on its own (a transfer-detail popup, an expanded table row) without the
// full transactions table around it.
export function TransferRowCard({ row }: { row: TransferRowInfo }) {
  return (
    <div className="flex flex-1 flex-col gap-0.5 rounded-md border p-2 text-sm">
      <span className="text-xs text-muted-foreground">{formatDate(row.postedAt.slice(0, 10))}</span>
      <span className="font-medium">{row.accountName}</span>
      <Truncate text={row.description} />
      <span className="tabular-nums">{formatCurrency(row.amount, row.currency)}</span>
    </div>
  )
}
