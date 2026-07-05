import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { CashflowSankeyChart } from '@/components/accounting/CashflowSankeyChart'
import { formatCurrency, formatMonthLong } from '@/lib/format'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import {
  useAccountingStore,
  useBudgetComparison,
  useCategoryTotals,
  useSetBudgets,
  useSuggestedBudgetAmount,
} from '@/hooks/useAccountingData'
import type { Budget, Category, CategoryTotalRow, CurrencyCode } from '@/types/accounting'

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7)
}

function monthBounds(month: string): { start: string; end: string } {
  const [year, mon] = month.split('-').map(Number)
  const start = `${month}-01`
  const end = new Date(year, mon, 0).toISOString().slice(0, 10)
  return { start, end }
}

function BudgetRow({
  category,
  month,
  draftAmount,
  actual,
  displayCurrency,
  onDraftChange,
}: {
  category: Category
  month: string
  draftAmount: string
  actual: number
  displayCurrency: CurrencyCode
  onDraftChange: (value: string) => void
}) {
  const { data: suggestion } = useSuggestedBudgetAmount(category.category_id, month)
  const budgeted = Number.parseFloat(draftAmount)
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
          value={draftAmount}
          placeholder="Not budgeted"
          onChange={(event) => onDraftChange(event.target.value)}
        />
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        {suggestion ? (
          <button
            type="button"
            className="hover:text-foreground hover:underline"
            title="Median actual spend over the last 3 months — click to use"
            onClick={() => onDraftChange(String(Math.round(suggestion.suggested_amount)))}
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
  const [month, setMonth] = useState(currentMonth())
  const [drafts, setDrafts] = useState<Record<string, string> | null>(null)

  const { data: store, isLoading: storeLoading } = useAccountingStore()
  const { data: comparison, isLoading: comparisonLoading } = useBudgetComparison(month, displayCurrency)
  const setBudgets = useSetBudgets()

  const { start, end } = monthBounds(month)
  const { data: actualCategoryTotals } = useCategoryTotals(start, end, undefined, undefined, displayCurrency)

  const expenseCategories = Object.values(store?.categories ?? {})
    .filter((category) => category.classification === 'expense' && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  const actualByCategory = new Map((actualCategoryTotals ?? []).map((row) => [row.category_id, row.amount]))
  const monthBudgets = (store?.budgets ?? []).filter((budget) => budget.month === month)
  const effectiveDrafts =
    drafts ?? Object.fromEntries(monthBudgets.map((budget) => [budget.category_id, String(budget.amount)]))

  function setDraft(categoryId: string, value: string) {
    setDrafts({ ...effectiveDrafts, [categoryId]: value })
  }

  async function handleSave() {
    const otherMonths = (store?.budgets ?? []).filter((budget) => budget.month !== month)
    const thisMonth: Budget[] = Object.entries(effectiveDrafts)
      .filter(([, value]) => value.trim() !== '' && !Number.isNaN(Number.parseFloat(value)))
      .map(([categoryId, value]) => ({
        budget_id: `${month}:${categoryId}`,
        month,
        category_id: categoryId,
        amount: Number.parseFloat(value),
        currency: displayCurrency,
      }))
    await setBudgets.mutateAsync([...otherMonths, ...thisMonth])
    setDrafts(null)
  }

  const budgetedTotal = comparison?.reduce((sum, row) => sum + row.budgeted, 0) ?? 0
  const actualTotal = comparison?.reduce((sum, row) => sum + row.actual, 0) ?? 0
  const budgetedSankeyRows: CategoryTotalRow[] = (comparison ?? []).map((row) => ({
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
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Budget</h1>
        <DisplayCurrencyToggle />
      </div>

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle>{formatMonthLong(month)}</CardTitle>
            <div className="flex items-center gap-2">
              <Input type="month" className="w-40" value={month} onChange={(event) => setMonth(event.target.value)} />
              <Button size="sm" onClick={handleSave} disabled={setBudgets.isPending || drafts === null}>
                {setBudgets.isPending ? 'Saving…' : 'Save budgets'}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
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
                      key={category.category_id}
                      category={category}
                      month={month}
                      draftAmount={effectiveDrafts[category.category_id] ?? ''}
                      actual={actualByCategory.get(category.category_id) ?? 0}
                      displayCurrency={displayCurrency}
                      onDraftChange={(value) => setDraft(category.category_id, value)}
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {!comparisonLoading && comparison && comparison.length > 0 && (
          <div className="grid gap-4 lg:grid-cols-2">
            <CashflowSankeyChart categoryTotals={actualCategoryTotals ?? []} displayCurrency={displayCurrency} title="Actual cash flow" />
            <CashflowSankeyChart categoryTotals={budgetedSankeyRows} displayCurrency={displayCurrency} title="Budgeted cash flow" />
          </div>
        )}

        {comparison && comparison.length > 0 && (
          <p className="text-center text-xs text-muted-foreground">
            Budgeted {formatCurrency(budgetedTotal, displayCurrency)} vs. actual {formatCurrency(actualTotal, displayCurrency)} across{' '}
            {comparison.length} budgeted categor{comparison.length === 1 ? 'y' : 'ies'} this month.
          </p>
        )}
      </div>
    </div>
  )
}
