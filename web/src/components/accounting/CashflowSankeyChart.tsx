import { Sankey, Tooltip } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { colorForIndex } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import type { CategoryTotalRow, CurrencyCode } from '@/types/accounting'

function buildSankeyData(rows: CategoryTotalRow[]) {
  const income = rows.filter((row) => row.classification === 'income').reduce((sum, row) => sum + row.amount, 0)
  const expenseByCategory = new Map<string, number>()
  for (const row of rows) {
    if (row.classification !== 'expense') continue
    expenseByCategory.set(row.category_name, (expenseByCategory.get(row.category_name) ?? 0) + row.amount)
  }
  const expenseCategories = [...expenseByCategory.entries()].sort((a, b) => b[1] - a[1])
  const totalExpense = expenseCategories.reduce((sum, [, value]) => sum + value, 0)
  const spent = Math.min(income, totalExpense)
  const saved = Math.max(income - totalExpense, 0)

  const nodes = [
    { name: 'Income', fill: '#0f172a' },
    { name: 'Spent', fill: '#dc2626' },
    ...(saved > 0 ? [{ name: 'Saved', fill: '#059669' }] : []),
    ...expenseCategories.map(([name], index) => ({ name, fill: colorForIndex(index) })),
  ]
  const spentIndex = 1
  const savedIndex = saved > 0 ? 2 : -1
  const firstCategoryIndex = saved > 0 ? 3 : 2

  const links = [
    ...(spent > 0 ? [{ source: 0, target: spentIndex, value: spent }] : []),
    ...(savedIndex >= 0 ? [{ source: 0, target: savedIndex, value: saved }] : []),
    ...expenseCategories.map(([, value], index) => ({
      source: spentIndex,
      target: firstCategoryIndex + index,
      value: (value / totalExpense) * spent || 0,
    })),
  ]

  return { nodes, links, overspent: totalExpense > income }
}

export function CashflowSankeyChart({
  categoryTotals,
  displayCurrency,
}: {
  categoryTotals: CategoryTotalRow[]
  displayCurrency: CurrencyCode
}) {
  const { nodes, links, overspent } = buildSankeyData(categoryTotals)

  return (
    <ChartCard
      title="Cash flow"
      description={overspent ? 'Expenses exceeded income this period' : 'Income in, spending and savings out'}
      isEmpty={links.length === 0}
    >
      <Sankey
        data={{ nodes, links }}
        link={{ stroke: '#cbd5e1', strokeOpacity: 0.5 }}
        nodePadding={20}
        margin={{ left: 8, right: 80, top: 8, bottom: 8 }}
      >
        <Tooltip formatter={(value) => formatCurrency(Number(value), displayCurrency)} />
      </Sankey>
    </ChartCard>
  )
}
