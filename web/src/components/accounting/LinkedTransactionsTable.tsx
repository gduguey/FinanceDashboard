import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowRight, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import type { ReactNode } from 'react'
import { useRef } from 'react'
import { TransferRowCard } from '@/components/accounting/TransferRowCard'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useExpandableRows } from '@/hooks/useExpandableRows'
import { useSortableRows } from '@/hooks/useSortableRows'
import { formatCurrency, formatDate } from '@/lib/format'
import type { LinkedPairRow } from '@/lib/transferRowInfo'

const ESTIMATED_ROW_HEIGHT = 45
const COLUMN_COUNT = 8

// Percentage widths for the 8 columns below, in order — always summing to
// 100 so the table (rendered `table-fixed`) never needs a horizontal
// scrollbar to show every column, regardless of container width. Content
// that doesn't fit is truncated with an ellipsis (see `Truncate`) rather
// than growing the column.
const COLUMN_WIDTHS = ['8%', '14%', '18%', '10%', '8%', '14%', '18%', '10%']

// A "from" transactions / "to" transactions table for a set of confirmed
// (or reconstructed) transfer pairs — virtualized and sortable-by-column-click
// like every other table in the app. Each row's click expands a fuller
// from/to card directly below it (multiple can stay open at once — see
// `useExpandableRows`), with a collapse/unfold-all toggle up top.
// `renderRowAction`, when given, renders next to the expanded cards (e.g. a
// "remove exclusion" button on the Excluded-from-rules page) — this table
// itself stays read-only otherwise.
//
// Each virtual "row" is actually its own `<tbody>` (a table can hold more
// than one, each an independent row-group) rather than a single `<tr>`, so
// its optional detail row can sit right inside the same measured group —
// the virtualizer's dynamic-size measurement then accounts for the expanded
// height automatically, the same way `TransactionsTab`'s row measurement
// already handles a variable-height AI-suggestion message.
export function LinkedTransactionsTable({
  rows,
  renderRowAction,
  emptyMessage = 'No transactions linked by this rule yet.',
}: {
  rows: LinkedPairRow[]
  renderRowAction?: (row: LinkedPairRow) => ReactNode
  emptyMessage?: string
}) {
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'fromPostedAt')
  const { expanded, toggle, collapseAll, unfoldAll } = useExpandableRows(rows.map((row) => row.linkId))
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
    return <p className="py-4 text-center text-sm text-muted-foreground">{emptyMessage}</p>
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
                active={sort.key === 'fromPostedAt'}
                desc={sort.desc}
                onClick={() => toggleSort('fromPostedAt')}
              >
                From date
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'fromAccountName'}
                desc={sort.desc}
                onClick={() => toggleSort('fromAccountName')}
              >
                From account
              </SortableTableHead>
              <TableHead>From description</TableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'fromAmount'}
                desc={sort.desc}
                onClick={() => toggleSort('fromAmount')}
              >
                From amount
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'toPostedAt'}
                desc={sort.desc}
                onClick={() => toggleSort('toPostedAt')}
              >
                To date
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'toAccountName'}
                desc={sort.desc}
                onClick={() => toggleSort('toAccountName')}
              >
                To account
              </SortableTableHead>
              <TableHead>To description</TableHead>
              <SortableTableHead
                align="right"
                active={sort.key === 'toAmount'}
                desc={sort.desc}
                onClick={() => toggleSort('toAmount')}
              >
                To amount
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
            const isExpanded = expanded.has(row.linkId)
            return (
              <TableBody key={row.linkId} ref={rowVirtualizer.measureElement} data-index={virtualRow.index}>
                <TableRow className="cursor-pointer" onClick={() => toggle(row.linkId)}>
                  <TableCell className="text-muted-foreground">
                    <Truncate text={formatDate(row.fromPostedAt.slice(0, 10))} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.fromAccountName} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.fromDescription} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <Truncate text={formatCurrency(row.fromAmount, row.fromCurrency)} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    <Truncate text={formatDate(row.toPostedAt.slice(0, 10))} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.toAccountName} />
                  </TableCell>
                  <TableCell>
                    <Truncate text={row.toDescription} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <Truncate text={formatCurrency(row.toAmount, row.toCurrency)} />
                  </TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={COLUMN_COUNT}>
                      <div className="flex flex-wrap items-center justify-between gap-3 py-1">
                        <div className="flex flex-1 flex-wrap items-center gap-3">
                          <TransferRowCard
                            row={{
                              transactionId: row.fromTransactionId,
                              accountName: row.fromAccountName,
                              description: row.fromDescription,
                              postedAt: row.fromPostedAt,
                              amount: row.fromAmount,
                              currency: row.fromCurrency,
                            }}
                          />
                          <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
                          <TransferRowCard
                            row={{
                              transactionId: row.toTransactionId,
                              accountName: row.toAccountName,
                              description: row.toDescription,
                              postedAt: row.toPostedAt,
                              amount: row.toAmount,
                              currency: row.toCurrency,
                            }}
                          />
                        </div>
                        {renderRowAction && (
                          // Pure event containment — it keeps the row's own
                          // handlers off the action control it wraps, and adds
                          // no behaviour of its own to expose.
                          <div
                            role="none"
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => event.stopPropagation()}
                          >
                            {renderRowAction(row)}
                          </div>
                        )}
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
