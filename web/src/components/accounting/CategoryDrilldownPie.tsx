import { ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { PieChartLegend } from '@/components/shared/PieChartLegend'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { TablePagination } from '@/components/shared/TablePagination'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { usePostingsPage } from '@/hooks/useAccountingData'
import { formatCurrency, formatDate } from '@/lib/format'
import { NO_SUBCATEGORY, PLACEHOLDER_ACCOUNT_IDS, UNCATEGORIZED } from '@/lib/transactionFilters'
import {
  type Account,
  type CategoryClassification,
  type CategoryTotalRow,
  type CurrencyCode,
  type Posting,
  type PostingFilters,
  type PostingSortField,
  UNCATEGORIZED_EXPENSE_CATEGORY_ID,
  UNCATEGORIZED_INCOME_CATEGORY_ID,
} from '@/types/accounting'

// Drilldown rows per page. Smaller than the transactions table's 200: this is
// a panel inside a chart card with its own scroll box, not a screen.
const PAGE_SIZE = 50

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
  /** The clicked ring slice's own value, already converted into the display currency. */
  total: number
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
        color: row.category_color,
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

/**
 * The clicked slice's own postings, as `GET /postings` selects them.
 *
 * Every predicate the browser used to evaluate over a resident ledger has a
 * parameter here. `income_expense` is the one worth naming: it asserts
 * `is_real_income_expense` *and* the sign, which is exactly what the client
 * was computing with `realIncomeExpensePostingIds` plus an `amount >= 0`
 * test, so the two-pass version is not translated, it is deleted.
 *
 * @param selection - The subcategory slice the user clicked.
 * @param scope - The period bar's window, account and tag, as the wire names them.
 * @returns The filter for this drilldown, with no sort or page window.
 */
function drilldownFilters(selection: SelectedSubcategory, scope: Required<PostingFilters>): Required<PostingFilters> {
  const isUncategorized =
    selection.categoryId === UNCATEGORIZED_INCOME_CATEGORY_ID ||
    selection.categoryId === UNCATEGORIZED_EXPENSE_CATEGORY_ID
  return {
    ...scope,
    income_expense: selection.classification,
    // "Uncategorized" is a ring slice, not a category: the rows behind it
    // carry no category at all, which the wire spells with its own sentinel
    // and which no subcategory predicate then narrows.
    categories: [isUncategorized ? UNCATEGORIZED : selection.categoryId],
    subcategories: isUncategorized ? [] : [selection.subcategoryId ?? NO_SUBCATEGORY],
  }
}

/**
 * Keep only the legs the drilldown is about, out of a page cut by transaction.
 *
 * `GET /postings` windows by transaction and returns **every** leg of each
 * one, placeholders included — `repositories.projection.page_rows` says so and
 * says why (the transfer badge and "undo split" both read a sibling), and the
 * transactions table drops the placeholders itself for the same reason. This
 * panel needs one narrowing more: a split transaction can have one leg under
 * the clicked subcategory and another somewhere else, and listing the second
 * under this heading would be a wrong row, not merely an extra one.
 *
 * That is three comparisons over one page, not a filter pass over a ledger —
 * the thing the endpoint cannot express is *which legs*, because its window is
 * a transaction. Sign and reality are not re-tested here: `income_expense`
 * already asserted both server-side, so a leg that reached this page and
 * carries the selected category is one the filter selected.
 *
 * @param items - One page of rows as the server returned them.
 * @param selection - The subcategory slice the user clicked.
 * @returns The rows this table lists.
 */
function matchedLegs(items: Posting[], selection: SelectedSubcategory): Posting[] {
  const isUncategorized =
    selection.categoryId === UNCATEGORIZED_INCOME_CATEGORY_ID ||
    selection.categoryId === UNCATEGORIZED_EXPENSE_CATEGORY_ID
  return items.filter((posting) => {
    if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) return false
    if (isUncategorized) return posting.category_id == null
    return posting.category_id === selection.categoryId && posting.subcategory_id === (selection.subcategoryId ?? null)
  })
}

function SubcategoryTable({
  selection,
  scope,
  accounts,
  displayCurrency,
}: {
  selection: SelectedSubcategory
  scope: Required<PostingFilters>
  accounts: Record<string, Account>
  displayCurrency: CurrencyCode
}) {
  const [sort, setSort] = useState<{ key: PostingSortField; desc: boolean }>({ key: 'posted_at', desc: true })
  const [offset, setOffset] = useState(0)
  const page = usePostingsPage({
    ...drilldownFilters(selection, scope),
    sort: sort.key,
    descending: sort.desc,
    limit: PAGE_SIZE,
    offset,
  })
  const rows = useMemo(() => matchedLegs(page.data?.items ?? [], selection), [page.data, selection])
  const matchedPostings = page.data?.counts?.matched_postings ?? 0

  function toggleSort(key: PostingSortField) {
    setSort((previous) => (previous.key === key ? { key, desc: !previous.desc } : { key, desc: true }))
    setOffset(0)
  }

  // The already-converted value of the ring slice this table was opened from,
  // not `sum(|amount|)` over the rows. Those amounts are each in their own
  // native currency, so adding them up and labelling the result the display
  // currency produced a number that was only ever right for a single-currency
  // ledger — and it cannot be computed here at all now that the rows arrive a
  // page at a time.
  const total = selection.total

  return (
    <div className="space-y-2">
      {/* "Rows", as the transactions toolbar says it and for the same reason:
          a split transaction is one transaction and several rows, and this
          line counts what the table lists. The pager below counts
          transactions, which is what a page is cut by. */}
      <p className="text-sm text-muted-foreground">
        {matchedPostings.toLocaleString()} row{matchedPostings === 1 ? '' : 's'} — total{' '}
        {formatCurrency(total, displayCurrency)}
      </p>
      <div className="max-h-96 overflow-y-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'posted_at'}
                desc={sort.desc}
                onClick={() => toggleSort('posted_at')}
              >
                Date
              </SortableTableHead>
              <SortableTableHead
                active={sort.key === 'account_id'}
                desc={sort.desc}
                onClick={() => toggleSort('account_id')}
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
              <SortableTableHead
                align="right"
                active={sort.key === 'amount'}
                desc={sort.desc}
                onClick={() => toggleSort('amount')}
              >
                % of subcategory
              </SortableTableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((posting) => (
              <TableRow key={posting.posting_id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDate(posting.posted_at.slice(0, 10))}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {accounts[posting.account_id]?.name ?? posting.account_id}
                </TableCell>
                <TableCell className="max-w-xs">
                  <Truncate text={posting.description} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCurrency(posting.amount, posting.currency)}
                </TableCell>
                {/* A share of the total is only a number when the two are in
                    the same currency: the row's amount is native and the total
                    is converted. It used to be printed regardless, against a
                    denominator that added the two together. */}
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {total && posting.currency === displayCurrency
                    ? `${((Math.abs(posting.amount) / total) * 100).toFixed(1)}%`
                    : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <TablePagination
        offset={offset}
        limit={PAGE_SIZE}
        total={page.data?.total ?? 0}
        unit="transaction"
        busy={page.isPlaceholderData}
        onOffsetChange={setOffset}
      />
    </div>
  )
}

export function CategoryDrilldownPie({
  categoryTotals,
  scope: periodScope,
  accounts,
  isLoading,
  displayCurrency,
}: {
  categoryTotals: CategoryTotalRow[]
  /** The period bar’s window, account and tag, already in the wire’s vocabulary. */
  scope: Required<PostingFilters>
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
        total: slice.value,
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
    const categoryName =
      categoryTotals.find((row) => row.category_id === scope.categoryId)?.category_name ?? scope.categoryId
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
                type="button"
                onClick={crumb.onClick}
                className={
                  index === breadcrumb.length - 1
                    ? 'font-medium text-foreground'
                    : 'hover:text-foreground hover:underline'
                }
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
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              ← back to chart
            </button>
            <p className="text-sm font-medium">
              {selected.categoryName} → {selected.subcategoryName}
            </p>
            <SubcategoryTable
              selection={selected}
              scope={periodScope}
              accounts={accounts}
              displayCurrency={displayCurrency}
            />
          </div>
        ) : !grandTotal ? (
          <div className="flex h-80 items-center justify-center text-sm text-muted-foreground">
            No transactions in this period
          </div>
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
                      showPercent && grandTotal
                        ? `${((numeric / grandTotal) * 100).toFixed(1)}%`
                        : formatCurrency(numeric, displayCurrency),
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
