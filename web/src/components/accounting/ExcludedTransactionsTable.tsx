import { useVirtualizer } from '@tanstack/react-virtual'
import { ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { useRef } from 'react'
import { TransferRowCard } from '@/components/accounting/TransferRowCard'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { useExpandableRows } from '@/hooks/useExpandableRows'
import { useSortableRows } from '@/hooks/useSortableRows'
import { formatCurrency, formatDate } from '@/lib/format'
import type { TransferRowInfo } from '@/lib/transferRowInfo'

const ESTIMATED_ROW_HEIGHT = 45
const COLUMN_COUNT = 4
// Always summing to 100 so the table (rendered `table-fixed`) never needs a
// horizontal scrollbar to show every column — see `LinkedTransactionsTable`'s
// own `COLUMN_WIDTHS` for the same convention.
const COLUMN_WIDTHS = ['15%', '25%', '40%', '20%']

// A rule's excluded transactions, one per row (unlike `LinkedTransactionsTable`,
// there's no "other side" — exclusion means this rule specifically never
// linked or repointed it) — same virtualized/sortable/click-to-expand
// conventions as every other table in the app, with a collapse/unfold-all
// toggle up top. Clicking a row expands a fuller card plus the action to
// remove that one exclusion, directly below it.
export function ExcludedTransactionsTable({
  rows,
  onRemoveExclusion,
}: {
  rows: TransferRowInfo[]
  onRemoveExclusion: (transactionId: string) => void
}) {
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'postedAt')
  const { expanded, toggle, collapseAll, unfoldAll } = useExpandableRows(rows.map((row) => row.transactionId))
  const scrollParentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: sorted.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 8,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  if (rows.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No transactions excluded from this rule.</p>
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end gap-1">
        <Button variant="ghost" size="sm" onClick={collapseAll}>
          <ChevronsDownUp className="size-3.5" />
          Collapse all
        </Button>
        <Button variant="ghost" size="sm" onClick={unfoldAll}>
          <ChevronsUpDown className="size-3.5" />
          Unfold all
        </Button>
      </div>
      <div ref={scrollParentRef} className="max-h-[50vh] overflow-y-auto rounded-md border">
        <Table className="table-fixed">
          <colgroup>
            {COLUMN_WIDTHS.map((width, index) => (
              <col key={index} style={{ width }} />
            ))}
          </colgroup>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'postedAt'}
                desc={sort.desc}
                onClick={() => toggleSort('postedAt')}
              >
                Date
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'accountName'}
                desc={sort.desc}
                onClick={() => toggleSort('accountName')}
              >
                Account
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'description'}
                desc={sort.desc}
                onClick={() => toggleSort('description')}
              >
                Description
              </SortableTableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'amount'}
                desc={sort.desc}
                onClick={() => toggleSort('amount')}
              >
                Amount
              </SortableTableHead>
            </TableRow>
          </TableHeader>
          {paddingTop > 0 && (
            <TableBody>
              <tr>
                <td colSpan={COLUMN_COUNT} style={{ height: paddingTop }} />
              </tr>
            </TableBody>
          )}
          {virtualRows.map((virtualRow) => {
            const row = sorted[virtualRow.index]
            const isExpanded = expanded.has(row.transactionId)
            return (
              <TableBody key={row.transactionId} ref={rowVirtualizer.measureElement} data-index={virtualRow.index}>
                <TableRow className="cursor-pointer" onClick={() => toggle(row.transactionId)}>
                  <TableCell className="text-muted-foreground">
                    <Truncate text={formatDate(row.postedAt.slice(0, 10))} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.accountName} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.description} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <Truncate text={formatCurrency(row.amount, row.currency)} />
                  </TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={COLUMN_COUNT}>
                      <div className="flex flex-wrap items-center justify-between gap-3 py-1">
                        <TransferRowCard row={row} />
                        <Button
                          variant="destructive"
                          size="sm"
                          title="Remove this exclusion — the rule will resolve this transaction again, if it still matches"
                          onClick={(event) => {
                            event.stopPropagation()
                            onRemoveExclusion(row.transactionId)
                          }}
                        >
                          Remove exclusion
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            )
          })}
          {paddingBottom > 0 && (
            <TableBody>
              <tr>
                <td colSpan={COLUMN_COUNT} style={{ height: paddingBottom }} />
              </tr>
            </TableBody>
          )}
        </Table>
      </div>
    </div>
  )
}
