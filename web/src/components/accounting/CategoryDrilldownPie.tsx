import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Cell, Pie, PieChart, Tooltip } from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { ResponsiveContainer } from 'recharts'
import { PieChartLegend } from '@/components/shared/PieChartLegend'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { formatCurrency, formatDate } from '@/lib/format'
import { realIncomeExpensePostingIds } from '@/lib/postingClassification'
import { useSortableRows } from '@/hooks/useSortableRows'
import {
  UNCATEGORIZED_EXPENSE_CATEGORY_ID,
  UNCATEGORIZED_INCOME_CATEGORY_ID,
  type Account,
  type CategoryClassification,
  type CategoryTotalRow,
  type CurrencyCode,
  type Posting,
} from '@/types/accounting'

interface Scope {
  classification?: CategoryClassification
  categoryId?: string
}

interface SelectedSubcategory {
  classification: CategoryClassification
  categoryId: string
  categoryName: string
  subcategoryId: string | null
  subcategoryName: string
}

interface RingSlice {
  key: string
  name: string
  value: number
  color: string
  classification: CategoryClassification
  categoryId?: string
  categoryName?: string
  subcategoryId?: string | null
  subcategoryName?: string
}

function classificationRing(rows: CategoryTotalRow[]): RingSlice[] {
  const totals = new Map<CategoryClassification, number>()
  for (const row of rows) totals.set(row.classification, (totals.get(row.classification) ?? 0) + row.amount)
  return [...totals.entries()].map(([classification, value]) => ({
    key: classification,
    name: classification === 'income' ? 'Income' : 'Expense',
    value,
    color: classification === 'income' ? '#059669' : '#dc2626',
    classification,
  }))
}

function categoryRing(rows: CategoryTotalRow[]): RingSlice[] {
  const byId = new Map<string, RingSlice>()
  for (const row of rows) {
    const existing = byId.get(row.category_id)
    if (existing) {
      existing.value += row.amount
    } else {
      byId.set(row.category_id, {
        key: row.category_id,
        name: row.category_name,
        value: row.amount,
        color: row.color,
        classification: row.classification,
        categoryId: row.category_id,
        categoryName: row.category_name,
      })
    }
  }
  return [...byId.values()]
}

function subcategoryRing(rows: CategoryTotalRow[]): RingSlice[] {
  const byKey = new Map<string, RingSlice>()
  for (const row of rows) {
    const key = `${row.category_id}::${row.subcategory_id ?? '__none__'}`
    const existing = byKey.get(key)
    if (existing) {
      existing.value += row.amount
    } else {
      byKey.set(key, {
        key,
        name: row.subcategory_name ?? `${row.category_name} (other)`,
        value: row.amount,
        color: row.color,
        classification: row.classification,
        categoryId: row.category_id,
        categoryName: row.category_name,
        subcategoryId: row.subcategory_id,
        subcategoryName: row.subcategory_name ?? `${row.category_name} (other)`,
      })
    }
  }
  return [...byKey.values()]
}

function scopedRows(rows: CategoryTotalRow[], scope: Scope): CategoryTotalRow[] {
  return rows.filter(
    (row) =>
      (!scope.classification || row.classification === scope.classification) &&
      (!scope.categoryId || row.category_id === scope.categoryId),
  )
}

function buildRings(rows: CategoryTotalRow[], scope: Scope): { level: string; slices: RingSlice[] }[] {
  if (scope.categoryId) {
    return [{ level: 'subcategory', slices: subcategoryRing(scopedRows(rows, scope)) }]
  }
  if (scope.classification) {
    const scoped = scopedRows(rows, scope)
    return [
      { level: 'category', slices: categoryRing(scoped) },
      { level: 'subcategory', slices: subcategoryRing(scoped) },
    ]
  }
  return [
    { level: 'classification', slices: classificationRing(rows) },
    { level: 'category', slices: categoryRing(rows) },
    { level: 'subcategory', slices: subcategoryRing(rows) },
  ]
}

const INNER_START = 40
const OUTER_END = 150

function SubcategoryTable({
  selection,
  postings,
  allPostings,
  accounts,
  displayCurrency,
}: {
  selection: SelectedSubcategory
  postings: Posting[]
  allPostings: Posting[]
  accounts: Record<string, Account>
  displayCurrency: CurrencyCode
}) {
  const isUncategorized =
    selection.categoryId === UNCATEGORIZED_INCOME_CATEGORY_ID || selection.categoryId === UNCATEGORIZED_EXPENSE_CATEGORY_ID
  const realIds = realIncomeExpensePostingIds(allPostings, accounts)
  const rows = postings.filter((posting) => {
    if (!realIds.has(posting.posting_id)) return false
    const signMatches = selection.classification === 'income' ? posting.amount >= 0 : posting.amount < 0
    if (!signMatches) return false
    if (isUncategorized) return posting.category_id === null
    return posting.category_id === selection.categoryId && posting.subcategory_id === selection.subcategoryId
  })
  const total = rows.reduce((sum, row) => sum + Math.abs(row.amount), 0)
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'posted_at')

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        {rows.length} transaction{rows.length === 1 ? '' : 's'} — total {formatCurrency(total, displayCurrency)}
      </p>
      <div className="max-h-96 overflow-y-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead active={sort.key === 'posted_at'} desc={sort.desc} onClick={() => toggleSort('posted_at')}>
                Date
              </SortableTableHead>
              <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
                Account
              </SortableTableHead>
              <SortableTableHead active={sort.key === 'description'} desc={sort.desc} onClick={() => toggleSort('description')}>
                Description
              </SortableTableHead>
              <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
                Amount
              </SortableTableHead>
              <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
                % of subcategory
              </SortableTableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((posting) => (
              <TableRow key={posting.posting_id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(posting.posted_at.slice(0, 10))}</TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{accounts[posting.account_id]?.name ?? posting.account_id}</TableCell>
                <TableCell className="max-w-xs truncate">{posting.description}</TableCell>
                <TableCell className="text-right tabular-nums">{formatCurrency(posting.amount, posting.currency)}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {total ? `${((Math.abs(posting.amount) / total) * 100).toFixed(1)}%` : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function CategoryDrilldownPie({
  categoryTotals,
  postings,
  allPostings,
  accounts,
  isLoading,
  displayCurrency,
}: {
  categoryTotals: CategoryTotalRow[]
  postings: Posting[]
  allPostings: Posting[]
  accounts: Record<string, Account>
  isLoading: boolean
  displayCurrency: CurrencyCode
}) {
  const [scope, setScope] = useState<Scope>({})
  const [showPercent, setShowPercent] = useState(false)
  const [selected, setSelected] = useState<SelectedSubcategory | null>(null)

  const rings = buildRings(categoryTotals, scope)
  const bandWidth = rings.length ? (OUTER_END - INNER_START) / rings.length : 0
  const grandTotal = rings.length ? rings[rings.length - 1].slices.reduce((sum, slice) => sum + slice.value, 0) : 0

  function handleSliceClick(level: string, slice: RingSlice) {
    if (level === 'classification') {
      setScope({ classification: slice.classification })
    } else if (level === 'category') {
      setScope({ classification: slice.classification, categoryId: slice.categoryId })
    } else {
      setSelected({
        classification: slice.classification,
        categoryId: slice.categoryId ?? '',
        categoryName: slice.categoryName ?? '',
        subcategoryId: slice.subcategoryId ?? null,
        subcategoryName: slice.subcategoryName ?? slice.name,
      })
    }
  }

  const breadcrumb: { label: string; onClick: () => void }[] = [{ label: 'All', onClick: () => setScope({}) }]
  if (scope.classification) {
    breadcrumb.push({
      label: scope.classification === 'income' ? 'Income' : 'Expense',
      onClick: () => setScope({ classification: scope.classification }),
    })
  }
  if (scope.categoryId) {
    const categoryName = categoryTotals.find((row) => row.category_id === scope.categoryId)?.category_name ?? scope.categoryId
    breadcrumb.push({ label: categoryName, onClick: () => setScope(scope) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Where the money went</CardTitle>
        <CardAction>
          <Button variant="outline" size="sm" onClick={() => setShowPercent((prev) => !prev)}>
            {showPercent ? 'Show amounts' : 'Show percentages'}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-1 text-sm text-muted-foreground">
          {breadcrumb.map((crumb, index) => (
            <span key={crumb.label} className="flex items-center gap-1">
              {index > 0 && <ChevronRight className="size-3" />}
              <button
                onClick={crumb.onClick}
                className={index === breadcrumb.length - 1 ? 'font-medium text-foreground' : 'hover:text-foreground hover:underline'}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </div>

        {isLoading ? (
          <Skeleton className="h-80 w-full" />
        ) : selected ? (
          <div className="space-y-2">
            <button onClick={() => setSelected(null)} className="text-xs text-muted-foreground hover:text-foreground hover:underline">
              ← back to chart
            </button>
            <p className="text-sm font-medium">
              {selected.categoryName} → {selected.subcategoryName}
            </p>
            <SubcategoryTable
              selection={selected}
              postings={postings}
              allPostings={allPostings}
              accounts={accounts}
              displayCurrency={displayCurrency}
            />
          </div>
        ) : !grandTotal ? (
          <div className="flex h-80 items-center justify-center text-sm text-muted-foreground">No transactions in this period</div>
        ) : (
          <div className="flex gap-4">
            <ResponsiveContainer width="100%" height={360} className="flex-1">
              <PieChart>
                {rings.map((ring, index) => {
                  const innerRadius = INNER_START + index * bandWidth + 2
                  const outerRadius = INNER_START + (index + 1) * bandWidth
                  return (
                    <Pie
                      key={ring.level}
                      data={ring.slices}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={innerRadius}
                      outerRadius={outerRadius}
                      paddingAngle={1}
                      onClick={(entry) => handleSliceClick(ring.level, entry as unknown as RingSlice)}
                      label={false}
                      cursor="pointer"
                    >
                      {ring.slices.map((slice) => (
                        <Cell key={slice.key} fill={slice.color} stroke="var(--card)" strokeWidth={1} />
                      ))}
                    </Pie>
                  )
                })}
                <Tooltip
                  formatter={(value, name) => {
                    const numeric = Number(value)
                    return [
                      showPercent && grandTotal ? `${((numeric / grandTotal) * 100).toFixed(1)}%` : formatCurrency(numeric, displayCurrency),
                      name,
                    ]
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
            <PieChartLegend
              total={grandTotal}
              slices={rings[0].slices}
              showPercent={showPercent}
              displayCurrency={displayCurrency}
              onSliceClick={(slice) => handleSliceClick(rings[0].level, slice)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
