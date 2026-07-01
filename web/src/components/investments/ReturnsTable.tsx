import { ArrowDown, ArrowUp } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDate, formatPercent, formatUsd, signColor } from '@/lib/format'
import { useReturns } from '@/hooks/usePortfolioData'
import type { ReturnRow } from '@/types/portfolio'

const COLUMNS: { key: keyof ReturnRow; label: string; align?: 'right' }[] = [
  { key: 'trade_date', label: 'Date' },
  { key: 'symbol', label: 'Symbol' },
  { key: 'price_paid', label: 'Price paid', align: 'right' },
  { key: 'current_price', label: 'Current', align: 'right' },
  { key: 'days_held', label: 'Days held', align: 'right' },
  { key: 'total_return_pct', label: 'Return', align: 'right' },
  { key: 'annualized_return_pct', label: 'Annualized', align: 'right' },
  { key: 'alpha_period_pct', label: 'Alpha vs. HYSA', align: 'right' },
]

function cellValue(row: ReturnRow, key: keyof ReturnRow): string {
  switch (key) {
    case 'trade_date':
      return formatDate(row.trade_date)
    case 'symbol':
      return row.symbol
    case 'price_paid':
    case 'current_price':
      return formatUsd(row[key])
    case 'days_held':
      return String(row.days_held)
    default:
      return formatPercent(row[key] as number)
  }
}

export function ReturnsTable() {
  const { data, isLoading, isError } = useReturns()
  const [sort, setSort] = useState<{ key: keyof ReturnRow; desc: boolean }>({
    key: 'trade_date',
    desc: true,
  })

  const sorted = useMemo(() => {
    if (!data) return []
    return [...data].sort((a, b) => {
      const [x, y] = [a[sort.key], b[sort.key]]
      const cmp = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
      return sort.desc ? -cmp : cmp
    })
  }, [data, sort])

  function toggleSort(key: keyof ReturnRow) {
    setSort((prev) => (prev.key === key ? { key, desc: !prev.desc } : { key, desc: true }))
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trade-level returns</CardTitle>
        <CardDescription>Every trade vs. a compounding HYSA benchmark over the same window</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : isError || !data?.length ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No returns yet — hit Sync to pull prices and trade history.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {COLUMNS.map((column) => (
                  <TableHead
                    key={column.key}
                    className={column.align === 'right' ? 'text-right' : ''}
                  >
                    <button
                      onClick={() => toggleSort(column.key)}
                      className="inline-flex items-center gap-1 hover:text-foreground"
                    >
                      {column.label}
                      {sort.key === column.key &&
                        (sort.desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                    </button>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row, index) => (
                <TableRow key={`${row.trade_date}-${row.symbol}-${index}`}>
                  {COLUMNS.map((column) => (
                    <TableCell
                      key={column.key}
                      className={
                        column.align === 'right'
                          ? `text-right tabular-nums ${
                              column.key === 'total_return_pct' ||
                              column.key === 'annualized_return_pct' ||
                              column.key === 'alpha_period_pct'
                                ? signColor(row[column.key] as number)
                                : ''
                            }`
                          : ''
                      }
                    >
                      {cellValue(row, column.key)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
