import { useEffect, useRef, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { PageHeader } from '@/components/layout/PageHeader'
import { MonthSelect, availableMonths } from '@/components/accounting/MonthSelect'
import { CashflowSankeyChart } from '@/components/accounting/CashflowSankeyChart'
import { formatCurrency } from '@/lib/format'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { usePersistedState } from '@/hooks/usePersistedState'
import {
  useAccountingStore,
  useCategoryTotals,
  usePostings,
  useSetBudgets,
  useSetGeneralBudgets,
  useSuggestedBudgetAmount,
} from '@/hooks/useAccountingData'
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
  month,
  initialAmount,
  actual,
  displayCurrency,
  onCommit,
}: {
  category: Category
  month: string
  initialAmount: string
  actual: number
  displayCurrency: CurrencyCode
  onCommit: (value: string) => void
}) {
  const [amount, setAmount] = useState(initialAmount)
  const dirtyRef = useRef(false)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit
  const { data: suggestion } = useSuggestedBudgetAmount(category.category_id, month)

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

  return (
    <TableRow>
      <TableCell className="flex items-center gap-1.5 font-medium">
        <span className="inline-block size-2 rounded-full" style={{ background: category.color }} />
        {category.name}
      </TableCell>
      <TableCell className="text-right">
        <Input
          type="number"
          inputMode="decimal"
          className="ml-auto w-28 text-right"
          value={amount}
          placeholder="Not budgeted"
          onChange={(event) => change(event.target.value)}
        />
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        {suggestion ? (
          <button
            type="button"
            className="hover:text-foreground hover:underline"
            title="Median actual spend over the last 3 months — click to use"
            onClick={() => change(String(Math.round(suggestion.suggested_amount)))}
          >
            {formatCurrency(suggestion.suggested_amount, displayCurrency)}
          </button>
        ) : (
          '—'
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">{formatCurrency(actual, displayCurrency)}</TableCell>
      <TableCell className={`text-right tabular-nums ${delta !== null && delta < 0 ? 'text-destructive' : 'text-muted-foreground'}`}>
        {delta === null ? '—' : formatCurrency(delta, displayCurrency)}
      </TableCell>
    </TableRow>
  )
}

export function BudgetPage() {
  const { displayCurrency } = useDisplayCurrency()
  const [mode, setMode] = usePersistedState<BudgetMode>('accounting.budget-mode', 'per_month')
  const [month, setMonth] = usePersistedState('accounting.budget-month', currentMonth())

  const { data: store, isLoading: storeLoading } = useAccountingStore()
  const { data: postings } = usePostings()
  const setBudgets = useSetBudgets()
  const setGeneralBudgets = useSetGeneralBudgets()

  const { start, end } = monthBounds(month)
  const { data: actualCategoryTotals } = useCategoryTotals(start, end, undefined, undefined, displayCurrency)

  const expenseCategories = Object.values(store?.categories ?? {})
    .filter((category) => category.classification === 'expense' && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  const actualByCategory = new Map((actualCategoryTotals ?? []).map((row) => [row.category_id, row.amount]))

  function budgetedAmountFor(categoryId: string): string {
    if (mode === 'general') {
      const general = store?.general_budgets[categoryId]
      return general ? String(general.amount) : ''
    }
    const perMonth = (store?.budgets ?? []).find((budget) => budget.month === month && budget.category_id === categoryId)
    return perMonth ? String(perMonth.amount) : ''
  }

  function commitAmount(categoryId: string, rawValue: string) {
    const amount = Number.parseFloat(rawValue)
    const isValid = rawValue.trim() !== '' && !Number.isNaN(amount)
    if (mode === 'general') {
      const next = { ...(store?.general_budgets ?? {}) }
      if (isValid) next[categoryId] = { category_id: categoryId, amount, currency: displayCurrency }
      else delete next[categoryId]
      setGeneralBudgets.mutate(next)
      return
    }
    const otherEntries = (store?.budgets ?? []).filter(
      (budget) => !(budget.month === month && budget.category_id === categoryId),
    )
    const thisEntry = isValid
      ? [{ budget_id: `${month}:${categoryId}`, month, category_id: categoryId, amount, currency: displayCurrency }]
      : []
    setBudgets.mutate([...otherEntries, ...thisEntry])
  }

  const comparison = expenseCategories
    .map((category) => {
      const budgetedText = budgetedAmountFor(category.category_id)
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
            <ExchangeRateSyncButton />
          </>
        }
      />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle>Budgets</CardTitle>
            <div className="flex items-center gap-2">
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
                  {expenseCategories.map((category) => (
                    <BudgetRow
                      key={`${mode}:${category.category_id}`}
                      category={category}
                      month={month}
                      initialAmount={budgetedAmountFor(category.category_id)}
                      actual={actualByCategory.get(category.category_id) ?? 0}
                      displayCurrency={displayCurrency}
                      onCommit={(value) => commitAmount(category.category_id, value)}
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {comparison.length > 0 && (
          <div className="grid gap-4 lg:grid-cols-2">
            <CashflowSankeyChart categoryTotals={actualCategoryTotals ?? []} displayCurrency={displayCurrency} title="Actual cash flow" />
            <CashflowSankeyChart categoryTotals={budgetedSankeyRows} displayCurrency={displayCurrency} title="Budgeted cash flow" />
          </div>
        )}

        {comparison.length > 0 && (
          <p className="text-center text-xs text-muted-foreground">
            Budgeted {formatCurrency(budgetedTotal, displayCurrency)} vs. actual {formatCurrency(actualTotal, displayCurrency)} across{' '}
            {comparison.length} budgeted categor{comparison.length === 1 ? 'y' : 'ies'} this month.
          </p>
        )}
      </div>
    </div>
  )
}
