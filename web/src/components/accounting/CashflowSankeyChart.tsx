import { Sankey, Tooltip } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { lighten } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import type { CategoryTotalRow, CurrencyCode } from '@/types/accounting'

interface SankeyNode {
  name: string
  fill: string
}

interface SankeyLink {
  source: number
  target: number
  value: number
}

// Recharts' Sankey wants every link as a {source, target} pair of node
// *indices* — building nodes as a plain list and appending as we go (rather
// than hand-tracking offsets that shift depending on which optional nodes
// exist this period) is what keeps this correct as the "Income
// buffer"/"Saved" nodes come and go.
function buildSankeyData(rows: CategoryTotalRow[]) {
  const income = rows.filter((row) => row.classification === 'income').reduce((sum, row) => sum + row.amount, 0)
  const expenseRows = rows.filter((row) => row.classification === 'expense')

  const categoryOrder: string[] = []
  const categoryTotal = new Map<string, number>()
  const categoryColor = new Map<string, string>()
  const subcategoryTotals = new Map<string, Map<string, number>>()
  for (const row of expenseRows) {
    if (!categoryTotal.has(row.category_name)) categoryOrder.push(row.category_name)
    categoryTotal.set(row.category_name, (categoryTotal.get(row.category_name) ?? 0) + row.amount)
    categoryColor.set(row.category_name, row.color)
    const subLabel = row.subcategory_name ?? 'Uncategorized'
    const subTotals = subcategoryTotals.get(row.category_name) ?? new Map<string, number>()
    subTotals.set(subLabel, (subTotals.get(subLabel) ?? 0) + row.amount)
    subcategoryTotals.set(row.category_name, subTotals)
  }

  const expenseCategories = categoryOrder
    .map((name): [string, number] => [name, categoryTotal.get(name) ?? 0])
    .sort((a, b) => b[1] - a[1])
  const totalExpense = expenseCategories.reduce((sum, [, value]) => sum + value, 0)
  const buffer = Math.max(totalExpense - income, 0)
  const saved = Math.max(income - totalExpense, 0)

  const nodes: SankeyNode[] = []
  function addNode(name: string, fill: string): number {
    const index = nodes.length
    nodes.push({ name, fill })
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

  const links: SankeyLink[] = [
    ...(bufferIndex >= 0 ? [{ source: bufferIndex, target: incomeIndex, value: buffer }] : []),
    ...(spentIndex >= 0 ? [{ source: incomeIndex, target: spentIndex, value: totalExpense }] : []),
    ...(savedIndex >= 0 ? [{ source: incomeIndex, target: savedIndex, value: saved }] : []),
  ]

  // Each category keeps its own real color (set once, in `categories.color`)
  // rather than an arbitrary palette slot, so the same category always
  // reads as the same color across this chart and the drilldown pie.
  // Subcategories only get their own tier when a category actually breaks
  // into more than one bucket — a single blob would just repeat the
  // category's own total one hop later — and each is a lighter shade of its
  // parent's color, darkest-to-lightest by size, instead of an unrelated hue.
  for (const [categoryName, categoryValue] of expenseCategories) {
    const baseColor = categoryColor.get(categoryName) ?? '#64748b'
    const categoryIndex = addNode(categoryName, baseColor)
    if (spentIndex >= 0) links.push({ source: spentIndex, target: categoryIndex, value: categoryValue })

    const subEntries = [...(subcategoryTotals.get(categoryName) ?? new Map()).entries()].sort((a, b) => b[1] - a[1])
    if (subEntries.length > 1) {
      subEntries.forEach(([label, amount], subIndex) => {
        const subcategoryIndex = addNode(label, lighten(baseColor, 0.2 + subIndex * 0.12))
        links.push({ source: categoryIndex, target: subcategoryIndex, value: amount })
      })
    }
  }

  return { nodes, links, isBuffered: buffer > 0 }
}

export function CashflowSankeyChart({
  categoryTotals,
  displayCurrency,
  title = 'Cash flow',
}: {
  categoryTotals: CategoryTotalRow[]
  displayCurrency: CurrencyCode
  title?: string
}) {
  const { nodes, links, isBuffered } = buildSankeyData(categoryTotals)

  return (
    <ChartCard
      title={title}
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
