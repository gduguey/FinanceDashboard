import { ChevronRight, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import { CashflowSankeyChart } from '@/components/accounting/CashflowSankeyChart'
import { availableMonths, MonthSelect } from '@/components/accounting/MonthSelect'
import { PageHeader } from '@/components/layout/PageHeader'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { NoAccountsYetBanner } from '@/components/shared/NoAccountsYetBanner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  useAccountingStore,
  useCategoryTotals,
  usePostings,
  useSetBudgets,
  useSetGeneralBudgets,
  useSuggestedBudgetAmount,
} from '@/hooks/useAccountingData'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { usePersistedState } from '@/hooks/usePersistedState'
import { withAlpha } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import type { Category, CategoryTotalRow, CurrencyCode } from '@/types/accounting'

type BudgetMode = 'general' | 'per_month'

const MODE_ITEMS: Record<BudgetMode, string> = { general: 'General', per_month: 'Per month' }
const COMMIT_DELAY_MS = 600

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7)
}

function monthBounds(month: string): { start: string; end: string } {
  const [year, mon] = month.split('-').map(Number)
  const start = `${month}-01`
  const end = new Date(year, mon, 0).toISOString().slice(0, 10)
  return { start, end }
}

// Auto-saves a category's budget input a moment after the user stops
// typing — no Save button anywhere on this page. Local state is its own
// (not lifted to the parent) so typing doesn't re-render the whole table,
// and it resyncs from `initialAmount` whenever that changes underneath it
// (switching months/modes, or another tab's edit coming back through
// `usePersistedState`-style invalidation).
function BudgetRow({
  category,
  topCategoryId,
  topCategoryColor,
  subcategoryId,
  month,
  initialAmount,
  actual,
  displayCurrency,
  expandable,
  onCommit,
}: {
  category: Category
  // The top-level category this row's suggestion/actual is scoped
  // under — same as `category.category_id` for a top-level row itself,
  // or the parent's id when `category` is one of its subcategories.
  topCategoryId: string
  // The top-level category's own color — same as `category.color` for a
  // top-level row, but the *parent's* color (not this subcategory's own
  // distinct one) for a subcategory row, since the row background is
  // meant to read as "part of this category" at a glance, while the
  // little dot still shows the subcategory's own color.
  topCategoryColor: string
  subcategoryId: string | null
  month: string
  initialAmount: string
  actual: number
  displayCurrency: CurrencyCode
  // Present only for a top-level row that has subcategories — renders
  // the expand/collapse chevron in place of the subcategory rows' indent.
  expandable?: { expanded: boolean; onToggle: () => void }
  onCommit: (value: string) => void
}) {
  const [amount, setAmount] = useState(initialAmount)
  const dirtyRef = useRef(false)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit
  const { data: suggestion } = useSuggestedBudgetAmount(topCategoryId, month, undefined, subcategoryId ?? undefined)

  useEffect(() => {
    setAmount(initialAmount)
    dirtyRef.current = false
  }, [initialAmount])

  useEffect(() => {
    if (!dirtyRef.current) return undefined
    const timeout = window.setTimeout(() => {
      onCommitRef.current(amount)
      dirtyRef.current = false
    }, COMMIT_DELAY_MS)
    return () => window.clearTimeout(timeout)
  }, [amount])

  function change(value: string) {
    dirtyRef.current = true
    setAmount(value)
  }

  const budgeted = Number.parseFloat(amount)
  const delta = Number.isNaN(budgeted) ? null : budgeted - actual
  const isSubcategory = subcategoryId !== null
  // Subcategory numbers pick up their parent category's color at reduced
  // opacity — a lighter shade of the same hue rather than an unrelated
  // muted gray, so the family relationship reads at a glance down the column.
  const numberStyle: React.CSSProperties = { color: category.color, opacity: isSubcategory ? 0.7 : 1 }
  const overspent = delta !== null && delta < 0
  const rowStyle: React.CSSProperties = { backgroundColor: withAlpha(topCategoryColor, 0.12) }

  return (
    <TableRow
      onClick={expandable ? expandable.onToggle : undefined}
      className={expandable ? 'cursor-pointer hover:brightness-95' : undefined}
      style={rowStyle}
    >
      <TableCell
        className={`flex items-center gap-1.5 ${isSubcategory ? 'py-1.5 pr-6 pl-9 text-xs font-normal text-muted-foreground' : 'font-medium'}`}
      >
        {expandable && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              expandable.onToggle()
            }}
            className="text-muted-foreground hover:text-foreground"
          >
            <ChevronRight className={`size-3.5 transition-transform ${expandable.expanded ? 'rotate-90' : ''}`} />
          </button>
        )}
        <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: category.color }} />
        {category.name}
      </TableCell>
      <TableCell
        className={`text-right ${isSubcategory ? 'py-1.5 pr-6' : ''}`}
        onClick={(event) => event.stopPropagation()}
      >
        <Input
          type="number"
          inputMode="decimal"
          className={`ml-auto text-right ${isSubcategory ? 'h-7 w-24 text-xs' : 'w-28'}`}
          value={amount}
          placeholder="Not budgeted"
          onChange={(event) => change(event.target.value)}
        />
      </TableCell>
      <TableCell
        className={`text-right ${isSubcategory ? 'py-1.5 pr-6 text-xs' : ''}`}
        style={numberStyle}
        onClick={(event) => event.stopPropagation()}
      >
        {suggestion ? (
          <button
            type="button"
            className="hover:underline"
            title="Median actual spend over the last 3 months — click to use"
            onClick={() => change(String(Math.round(suggestion.suggested_amount)))}
          >
            {formatCurrency(suggestion.suggested_amount, displayCurrency)}
          </button>
        ) : (
          '—'
        )}
      </TableCell>
      <TableCell
        className={`text-right tabular-nums ${isSubcategory ? 'py-1.5 pr-6 text-xs' : ''}`}
        style={numberStyle}
      >
        {formatCurrency(actual, displayCurrency)}
      </TableCell>
      <TableCell
        className={`text-right tabular-nums ${isSubcategory ? 'py-1.5 pr-6 text-xs' : ''} ${overspent ? 'text-destructive' : ''}`}
        style={overspent ? undefined : numberStyle}
      >
        {delta === null ? '—' : formatCurrency(delta, displayCurrency)}
      </TableCell>
    </TableRow>
  )
}

export function BudgetPage() {
  const { displayCurrency } = useDisplayCurrency()
  const [mode, setMode] = usePersistedState<BudgetMode>('accounting.budget-mode', 'per_month')
  const [month, setMonth] = usePersistedState('accounting.budget-month', currentMonth())
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const { data: store, isLoading: storeLoading } = useAccountingStore()
  const { data: postings } = usePostings()
  const setBudgets = useSetBudgets()
  const setGeneralBudgets = useSetGeneralBudgets()

  const { start, end } = monthBounds(month)
  const { data: actualCategoryTotals } = useCategoryTotals(start, end, undefined, undefined, displayCurrency)

  const expenseCategories = Object.values(store?.categories ?? {})
    .filter((category) => category.classification === 'expense' && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  const subcategoriesByParent = new Map<string, Category[]>()
  for (const category of Object.values(store?.categories ?? {})) {
    if (category.classification !== 'expense' || !category.parent_category_id) continue
    const siblings = subcategoriesByParent.get(category.parent_category_id) ?? []
    siblings.push(category)
    subcategoriesByParent.set(category.parent_category_id, siblings)
  }
  for (const siblings of subcategoriesByParent.values()) siblings.sort((a, b) => a.name.localeCompare(b.name))
  const expandableCategoryIds = expenseCategories
    .filter((category) => (subcategoriesByParent.get(category.category_id)?.length ?? 0) > 0)
    .map((category) => category.category_id)

  function toggleExpanded(categoryId: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(categoryId)) next.delete(categoryId)
      else next.add(categoryId)
      return next
    })
  }

  // Sums every actual row (one per category/subcategory pair) both by its
  // top-level category and, when it has one, its subcategory — a category
  // row's actual must include every subcategory's spend, not just the last
  // row a naive single-key map would have kept.
  const actualByCategory = new Map<string, number>()
  const actualBySubcategory = new Map<string, number>()
  for (const row of actualCategoryTotals ?? []) {
    actualByCategory.set(row.category_id, (actualByCategory.get(row.category_id) ?? 0) + row.amount)
    if (row.subcategory_id) {
      actualBySubcategory.set(row.subcategory_id, (actualBySubcategory.get(row.subcategory_id) ?? 0) + row.amount)
    }
  }

  function budgetedAmountFor(categoryId: string, subcategoryId: string | null): string {
    if (mode === 'general') {
      const general = store?.general_budgets[subcategoryId ?? categoryId]
      return general ? String(general.amount) : ''
    }
    const perMonth = (store?.budgets ?? []).find(
      (budget) =>
        budget.month === month &&
        budget.category_id === categoryId &&
        (budget.subcategory_id ?? null) === subcategoryId,
    )
    return perMonth ? String(perMonth.amount) : ''
  }

  function commitAmount(categoryId: string, subcategoryId: string | null, rawValue: string) {
    const amount = Number.parseFloat(rawValue)
    const isValid = rawValue.trim() !== '' && !Number.isNaN(amount)
    if (mode === 'general') {
      const key = subcategoryId ?? categoryId
      const next = { ...(store?.general_budgets ?? {}) }
      if (isValid)
        next[key] = { category_id: categoryId, subcategory_id: subcategoryId, amount, currency: displayCurrency }
      else delete next[key]
      setGeneralBudgets.mutate(next)
      return
    }
    const otherEntries = (store?.budgets ?? []).filter(
      (budget) =>
        !(
          budget.month === month &&
          budget.category_id === categoryId &&
          (budget.subcategory_id ?? null) === subcategoryId
        ),
    )
    const budgetId = subcategoryId ? `${month}:${categoryId}:${subcategoryId}` : `${month}:${categoryId}`
    const thisEntry = isValid
      ? [
          {
            budget_id: budgetId,
            month,
            category_id: categoryId,
            subcategory_id: subcategoryId,
            amount,
            currency: displayCurrency,
          },
        ]
      : []
    setBudgets.mutate([...otherEntries, ...thisEntry])
  }

  const comparison = expenseCategories
    .map((category) => {
      const budgetedText = budgetedAmountFor(category.category_id, null)
      const budgeted = Number.parseFloat(budgetedText)
      if (budgetedText.trim() === '' || Number.isNaN(budgeted)) return null
      return {
        category_id: category.category_id,
        category_name: category.name,
        color: category.color,
        budgeted,
        actual: actualByCategory.get(category.category_id) ?? 0,
      }
    })
    .filter((row): row is NonNullable<typeof row> => row !== null)

  const budgetedTotal = comparison.reduce((sum, row) => sum + row.budgeted, 0)
  const actualTotal = comparison.reduce((sum, row) => sum + row.actual, 0)
  const budgetedSankeyRows: CategoryTotalRow[] = comparison.map((row) => ({
    classification: 'expense',
    category_id: row.category_id,
    category_name: row.category_name,
    subcategory_id: null,
    subcategory_name: null,
    color: row.color,
    category_color: row.color,
    amount: row.budgeted,
  }))
  const totalIncome = (actualCategoryTotals ?? [])
    .filter((row) => row.classification === 'income')
    .reduce((sum, row) => sum + row.amount, 0)
  if (budgetedSankeyRows.length > 0) {
    budgetedSankeyRows.unshift({
      classification: 'income',
      category_id: '__income__',
      category_name: 'Income',
      subcategory_id: null,
      subcategory_name: null,
      color: '#0f172a',
      category_color: '#0f172a',
      amount: totalIncome,
    })
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Budget"
        actions={
          <>
            <DisplayCurrencyToggle />
          </>
        }
      />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {!storeLoading && store && !hasAnyRealAccount(Object.values(store.accounts)) && <NoAccountsYetBanner />}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle>Budgets</CardTitle>
            <div className="flex items-center gap-2">
              {expandableCategoryIds.length > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setExpandedIds(
                      expandedIds.size < expandableCategoryIds.length ? new Set(expandableCategoryIds) : new Set(),
                    )
                  }
                  title={expandedIds.size < expandableCategoryIds.length ? 'Expand all' : 'Collapse all'}
                >
                  {expandedIds.size < expandableCategoryIds.length ? (
                    <ChevronsUpDown className="size-3.5" />
                  ) : (
                    <ChevronsDownUp className="size-3.5" />
                  )}
                </Button>
              )}
              <Select value={mode} onValueChange={(value) => value && setMode(value as BudgetMode)}>
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue items={MODE_ITEMS} />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(MODE_ITEMS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <MonthSelect value={month} onChange={setMonth} months={availableMonths(postings ?? [])} />
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {mode === 'general'
                ? 'One budget per category, applied to every month — the month picker only changes which month’s actual spend is shown here.'
                : `Budgets scoped to this one month — actual spend below is for this month too.`}
            </p>
            {storeLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : expenseCategories.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No expense categories yet.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Budgeted</TableHead>
                    <TableHead className="text-right">Suggested</TableHead>
                    <TableHead className="text-right">Actual</TableHead>
                    <TableHead className="text-right">Remaining</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {expenseCategories.map((category) => {
                    const subcategories = subcategoriesByParent.get(category.category_id) ?? []
                    const expanded = expandedIds.has(category.category_id)
                    return (
                      <Fragment key={`${mode}:${category.category_id}`}>
                        <BudgetRow
                          category={category}
                          topCategoryId={category.category_id}
                          topCategoryColor={category.color}
                          subcategoryId={null}
                          month={month}
                          initialAmount={budgetedAmountFor(category.category_id, null)}
                          actual={actualByCategory.get(category.category_id) ?? 0}
                          displayCurrency={displayCurrency}
                          expandable={
                            subcategories.length > 0
                              ? { expanded, onToggle: () => toggleExpanded(category.category_id) }
                              : undefined
                          }
                          onCommit={(value) => commitAmount(category.category_id, null, value)}
                        />
                        {expanded &&
                          subcategories.map((subcategory) => (
                            <BudgetRow
                              key={`${mode}:${subcategory.category_id}`}
                              category={subcategory}
                              topCategoryId={category.category_id}
                              topCategoryColor={category.color}
                              subcategoryId={subcategory.category_id}
                              month={month}
                              initialAmount={budgetedAmountFor(category.category_id, subcategory.category_id)}
                              actual={actualBySubcategory.get(subcategory.category_id) ?? 0}
                              displayCurrency={displayCurrency}
                              onCommit={(value) => commitAmount(category.category_id, subcategory.category_id, value)}
                            />
                          ))}
                      </Fragment>
                    )
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {comparison.length > 0 && (
          // Stacked rather than side by side — a Sankey needs real
          // horizontal room for its flows to stay readable, which a
          // half-width grid column doesn't leave it.
          <div className="space-y-4">
            <CashflowSankeyChart
              categoryTotals={actualCategoryTotals ?? []}
              displayCurrency={displayCurrency}
              title="Actual cash flow"
            />
            <CashflowSankeyChart
              categoryTotals={budgetedSankeyRows}
              displayCurrency={displayCurrency}
              title="Budgeted cash flow"
            />
          </div>
        )}

        {comparison.length > 0 && (
          <p className="text-center text-xs text-muted-foreground">
            Budgeted {formatCurrency(budgetedTotal, displayCurrency)} vs. actual{' '}
            {formatCurrency(actualTotal, displayCurrency)} across {comparison.length} budgeted categor
            {comparison.length === 1 ? 'y' : 'ies'} this month.
          </p>
        )}
      </div>
    </div>
  )
}
