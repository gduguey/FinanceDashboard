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

const INCOME_ROOT_COLOR = '#0f172a'

// Groups one classification's rows into (category -> total) and
// (category -> subcategory -> total) maps, preserving first-seen category
// order (later sorted by size by the caller).
function groupByCategory(rows: CategoryTotalRow[]) {
  const order: string[] = []
  const categoryTotal = new Map<string, number>()
  const categoryColor = new Map<string, string>()
  const subcategoryTotals = new Map<string, Map<string, number>>()
  for (const row of rows) {
    if (!categoryTotal.has(row.category_name)) order.push(row.category_name)
    categoryTotal.set(row.category_name, (categoryTotal.get(row.category_name) ?? 0) + row.amount)
    categoryColor.set(row.category_name, row.color)
    const subLabel = row.subcategory_name ?? 'Uncategorized'
    const subTotals = subcategoryTotals.get(row.category_name) ?? new Map<string, number>()
    subTotals.set(subLabel, (subTotals.get(subLabel) ?? 0) + row.amount)
    subcategoryTotals.set(row.category_name, subTotals)
  }
  const categories = order
    .map((name): [string, number] => [name, categoryTotal.get(name) ?? 0])
    .sort((a, b) => b[1] - a[1])
  return { categories, categoryColor, subcategoryTotals }
}

// Recharts' Sankey wants every link as a {source, target} pair of node
// *indices* — building nodes as a plain list and appending as we go (rather
// than hand-tracking offsets that shift depending on which optional nodes
// exist this period) is what keeps this correct as the "Income
// buffer"/"Saved" nodes come and go.
//
// Coloring is two-level on both sides, but the level that carries the
// "root" color differs: income's *classification* itself is the anchor —
// one color for all of income, categories are shades of it, and a
// subcategory is exactly its parent's shade (not tinted further, since
// income rarely breaks down enough for a third distinct shade to read
// clearly). Expense's classification carries no color of its own; each
// expense *category* has its own real color, and subcategories are shades
// of that — the same convention `CategoryDrilldownPie` uses.
function buildSankeyData(rows: CategoryTotalRow[]) {
  const { categories: incomeCategories, subcategoryTotals: incomeSubcategoryTotals } = groupByCategory(
    rows.filter((row) => row.classification === 'income'),
  )
  const { categories: expenseCategories, categoryColor: expenseCategoryColor, subcategoryTotals: expenseSubcategoryTotals } =
    groupByCategory(rows.filter((row) => row.classification === 'expense'))

  const income = incomeCategories.reduce((sum, [, value]) => sum + value, 0)
  const totalExpense = expenseCategories.reduce((sum, [, value]) => sum + value, 0)
  const buffer = Math.max(totalExpense - income, 0)
  const saved = Math.max(income - totalExpense, 0)

  const nodes: SankeyNode[] = []
  function addNode(name: string, fill: string): number {
    const index = nodes.length
    nodes.push({ name, fill })
    return index
  }

  const links: SankeyLink[] = []

  // A buffer node feeding into Income (rather than an "overspent" message)
  // keeps the diagram itself always balanced: total in always equals total
  // out, whether this period's spending came entirely from income or ran
  // past it.
  const incomeIndex = addNode('Income', INCOME_ROOT_COLOR)
  incomeCategories.forEach(([categoryName, categoryValue], index) => {
    const shade = lighten(INCOME_ROOT_COLOR, 0.25 + index * 0.12)
    const categoryIndex = addNode(categoryName, shade)
    links.push({ source: categoryIndex, target: incomeIndex, value: categoryValue })

    const subEntries = [...(incomeSubcategoryTotals.get(categoryName) ?? new Map()).entries()].sort((a, b) => b[1] - a[1])
    if (subEntries.length > 1) {
      subEntries.forEach(([label, amount]) => {
        // Exactly the parent's shade, per the user's own convention for
        // income — a subcategory isn't a further tint, just the same color.
        const subcategoryIndex = addNode(label, shade)
        links.push({ source: subcategoryIndex, target: categoryIndex, value: amount })
      })
    }
  })

  const bufferIndex = buffer > 0 ? addNode('Income buffer', '#f59e0b') : -1
  if (bufferIndex >= 0) links.push({ source: bufferIndex, target: incomeIndex, value: buffer })

  const spentIndex = totalExpense > 0 ? addNode('Spent', '#dc2626') : -1
  const savedIndex = saved > 0 ? addNode('Saved', '#059669') : -1
  if (spentIndex >= 0) links.push({ source: incomeIndex, target: spentIndex, value: totalExpense })
  if (savedIndex >= 0) links.push({ source: incomeIndex, target: savedIndex, value: saved })

  for (const [categoryName, categoryValue] of expenseCategories) {
    const baseColor = expenseCategoryColor.get(categoryName) ?? '#64748b'
    const categoryIndex = addNode(categoryName, baseColor)
    if (spentIndex >= 0) links.push({ source: spentIndex, target: categoryIndex, value: categoryValue })

    const subEntries = [...(expenseSubcategoryTotals.get(categoryName) ?? new Map()).entries()].sort((a, b) => b[1] - a[1])
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
