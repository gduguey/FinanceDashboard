import { Sankey, Tooltip } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { colorForIndex } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import type { CategoryTotalRow, CurrencyCode } from '@/types/accounting'

interface SankeyNode {
  name: string
  fill: string
}

// Recharts' Sankey wants every link as a {source, target} pair of node
// *indices* — building nodes as a plain list and looking up "the index I
// just added" by name (rather than hand-tracking offsets that shift
// depending on which optional nodes exist this period) is what keeps this
// correct as the "Income buffer"/"Saved" nodes come and go.
function buildSankeyData(rows: CategoryTotalRow[]) {
  const income = rows.filter((row) => row.classification === 'income').reduce((sum, row) => sum + row.amount, 0)
  const expenseByCategory = new Map<string, number>()
  for (const row of rows) {
    if (row.classification !== 'expense') continue
    expenseByCategory.set(row.category_name, (expenseByCategory.get(row.category_name) ?? 0) + row.amount)
  }
  const expenseCategories = [...expenseByCategory.entries()].sort((a, b) => b[1] - a[1])
  const totalExpense = expenseCategories.reduce((sum, [, value]) => sum + value, 0)
  const buffer = Math.max(totalExpense - income, 0)
  const saved = Math.max(income - totalExpense, 0)

  const nodes: SankeyNode[] = []
  const indexOf = new Map<string, number>()
  function addNode(name: string, fill: string): number {
    const index = nodes.length
    nodes.push({ name, fill })
    indexOf.set(name, index)
    return index
  }

  // A buffer node feeding into Income (rather than an "overspent" message)
  // keeps the diagram itself always balanced: total in always equals total
  // out, whether this period's spending came entirely from income or ran
  // past it.
  const bufferIndex = buffer > 0 ? addNode('Income buffer', '#f59e0b') : -1
  const incomeIndex = addNode('Income', '#0f172a')
  const spentIndex = totalExpense > 0 ? addNode('Spent', '#dc2626') : -1
  const savedIndex = saved > 0 ? addNode('Saved', '#059669') : -1
  const categoryIndexes = expenseCategories.map(([name], index) => addNode(name, colorForIndex(index)))

  const links = [
    ...(bufferIndex >= 0 ? [{ source: bufferIndex, target: incomeIndex, value: buffer }] : []),
    ...(spentIndex >= 0 ? [{ source: incomeIndex, target: spentIndex, value: totalExpense }] : []),
    ...(savedIndex >= 0 ? [{ source: incomeIndex, target: savedIndex, value: saved }] : []),
    ...expenseCategories.map(([, value], index) => ({ source: spentIndex, target: categoryIndexes[index], value })),
  ]

  return { nodes, links, isBuffered: buffer > 0 }
}

export function CashflowSankeyChart({
  categoryTotals,
  displayCurrency,
}: {
  categoryTotals: CategoryTotalRow[]
  displayCurrency: CurrencyCode
}) {
  const { nodes, links, isBuffered } = buildSankeyData(categoryTotals)

  return (
    <ChartCard
      title="Cash flow"
      description={
        isBuffered
          ? 'Expenses exceeded income this period — shown as an "Income buffer" topping up income to match spending'
          : 'Income in, spending and savings out'
      }
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
