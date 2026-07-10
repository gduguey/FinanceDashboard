import { useId } from 'react'
import { Sankey, Tooltip } from 'recharts'
import type { LinkProps, NodeProps } from 'recharts/types/chart/Sankey'
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
  // Every link is colored explicitly at construction time (see
  // `buildSankeyData`'s own docstring) rather than left for the renderer
  // to guess from its endpoints — some flows (Income -> Spent, Saved ->
  // goal) need a color that belongs to neither endpoint's own node fill.
  color: string
  dashed?: boolean
}

// The single anchor color every income category is a shade of — distinct
// from the muted grey "Income buffer" flow, so overspending reads as
// clearly different from real income rather than just a darker income shade.
const INCOME_COLOR = '#2563eb'
const INCOME_BUFFER_COLOR = '#94a3b8'
const SAVED_COLOR = '#059669'
const UNALLOCATED_COLOR = '#a7f3d0'

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
    categoryColor.set(row.category_name, row.category_color)
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
// of that — the same convention `CategoryDrilldownPie` uses. Every flow's
// own color is decided here, at construction, rather than inferred later
// from its endpoints — several flows (Income -> Spent, Saved -> a goal)
// need a color that isn't simply "the node it touches".
export interface GoalFlow {
  name: string
  value: number
  color: string
}

function buildSankeyData(rows: CategoryTotalRow[], goalFlows: GoalFlow[]) {
  const { categories: incomeCategories, subcategoryTotals: incomeSubcategoryTotals } = groupByCategory(
    rows.filter((row) => row.classification === 'income'),
  )
  const {
    categories: expenseCategories,
    categoryColor: expenseCategoryColor,
    subcategoryTotals: expenseSubcategoryTotals,
  } = groupByCategory(rows.filter((row) => row.classification === 'expense'))

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
  function addLink(source: number, target: number, value: number, color: string, dashed = false) {
    links.push({ source, target, value, color, dashed })
  }

  // A buffer node feeding into Income (rather than an "overspent" message)
  // keeps the diagram itself always balanced: total in always equals total
  // out, whether this period's spending came entirely from income or ran
  // past it.
  const incomeIndex = addNode('Income', INCOME_COLOR)
  incomeCategories.forEach(([categoryName, categoryValue], index) => {
    const shade = lighten(INCOME_COLOR, 0.25 + index * 0.12)
    const categoryIndex = addNode(categoryName, shade)
    addLink(categoryIndex, incomeIndex, categoryValue, shade)

    const subEntries = [...(incomeSubcategoryTotals.get(categoryName) ?? new Map()).entries()].sort(
      (a, b) => b[1] - a[1],
    )
    if (subEntries.length > 1) {
      subEntries.forEach(([label, amount]) => {
        // Exactly the parent's shade, per the user's own convention for
        // income — a subcategory isn't a further tint, just the same color.
        const subcategoryIndex = addNode(label, shade)
        addLink(subcategoryIndex, categoryIndex, amount, shade)
      })
    }
  })

  // Deliberately grey/dashed rather than another income shade — this flow
  // represents a shortfall covered from elsewhere, not real income, and
  // must read as visually distinct from it at a glance.
  const bufferIndex = buffer > 0 ? addNode('Income buffer', INCOME_BUFFER_COLOR) : -1
  if (bufferIndex >= 0) addLink(bufferIndex, incomeIndex, buffer, INCOME_BUFFER_COLOR, true)

  const spentIndex = totalExpense > 0 ? addNode('Spent', '#dc2626') : -1
  const savedIndex = saved > 0 ? addNode('Saved', SAVED_COLOR) : -1
  // "Spent" carries the same anchor color as income itself (the user's own
  // "income spent is that color" convention) — "Saved" is the one flow
  // that gets a distinct color, since it heads somewhere entirely different.
  if (spentIndex >= 0) addLink(incomeIndex, spentIndex, totalExpense, INCOME_COLOR)
  if (savedIndex >= 0) addLink(incomeIndex, savedIndex, saved, SAVED_COLOR)

  // "Saved" splits further into each goal's contributions this period,
  // plus whatever's left unallocated — the same data `GoalsOverviewCharts`
  // shows as a pie for this period, just wired into the Sankey's
  // right-hand side instead. Only shown when goal contributions this
  // period don't exceed what was saved this period (the common case) —
  // if more went into goals than was saved (drawing on an earlier
  // period's leftover), the split can't be drawn as a clean subset of
  // this one link without a second synthetic inflow, so it's skipped
  // rather than drawn misleadingly.
  const totalGoalFlow = goalFlows.reduce((sum, flow) => sum + flow.value, 0)
  if (savedIndex >= 0 && totalGoalFlow > 0 && totalGoalFlow <= saved) {
    for (const flow of goalFlows) {
      const goalIndex = addNode(flow.name, flow.color)
      addLink(savedIndex, goalIndex, flow.value, flow.color)
    }
    const unallocated = saved - totalGoalFlow
    if (unallocated > 0) {
      const unallocatedIndex = addNode('Unallocated', UNALLOCATED_COLOR)
      addLink(savedIndex, unallocatedIndex, unallocated, UNALLOCATED_COLOR)
    }
  }

  for (const [categoryName, categoryValue] of expenseCategories) {
    const baseColor = expenseCategoryColor.get(categoryName) ?? '#64748b'
    const categoryIndex = addNode(categoryName, baseColor)
    if (spentIndex >= 0) addLink(spentIndex, categoryIndex, categoryValue, baseColor)

    const subEntries = [...(expenseSubcategoryTotals.get(categoryName) ?? new Map()).entries()].sort(
      (a, b) => b[1] - a[1],
    )
    if (subEntries.length > 1) {
      subEntries.forEach(([label, amount], subIndex) => {
        const shade = lighten(baseColor, 0.2 + subIndex * 0.12)
        const subcategoryIndex = addNode(label, shade)
        addLink(categoryIndex, subcategoryIndex, amount, shade)
      })
    }
  }

  return { nodes, links, isBuffered: buffer > 0 }
}

const HATCH_TILE_PX = 6

// Recharts' own default link renderer ignores each link's own data and
// paints every flow the same flat grey (see the library's `renderLinkItem`)
// — this mirrors that same curve geometry, but reads `color`/`dashed` back
// off `payload` (the link object `buildSankeyData` produced) so each flow
// carries the color decided for it above instead of one uniform stroke. A
// "dashed" flow (just the income buffer, so far) is painted with a dense
// 45°-diagonal hatch pattern over its own solid color — reading as a
// "blocked"/"not usable" zone — instead of a plain flat band, so it's
// unmistakably the odd one out. `patternId` is per-chart-instance (see
// `useId` in `CashflowSankeyChart`), since two Sankeys on one page (Budget's
// "Actual"/"Budgeted" pair) would otherwise both define an element with the
// same id.
function makeSankeyLinkPath(patternId: string) {
  return function SankeyLinkPath(props: LinkProps) {
    const { sourceX, sourceY, sourceControlX, targetX, targetY, targetControlX, linkWidth, payload } = props
    const linkPayload = payload as unknown as SankeyLink
    const d = `M${sourceX},${sourceY}C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`
    if (!linkPayload.dashed) {
      return (
        <path
          className="recharts-sankey-link"
          d={d}
          fill="none"
          stroke={linkPayload.color}
          strokeWidth={linkWidth}
          strokeOpacity={0.35}
        />
      )
    }
    return (
      <g>
        <defs>
          <pattern
            id={patternId}
            width={HATCH_TILE_PX}
            height={HATCH_TILE_PX}
            patternTransform="rotate(45)"
            patternUnits="userSpaceOnUse"
          >
            <rect width={HATCH_TILE_PX} height={HATCH_TILE_PX} fill={linkPayload.color} />
            <line x1={0} y1={0} x2={0} y2={HATCH_TILE_PX} stroke="#000000" strokeOpacity={0.5} strokeWidth={2.5} />
          </pattern>
        </defs>
        <path
          className="recharts-sankey-link"
          d={d}
          fill="none"
          stroke={`url(#${patternId})`}
          strokeWidth={linkWidth}
          strokeOpacity={0.9}
        />
      </g>
    )
  }
}

// Recharts computes `value`/`sourceLinks`/`targetLinks` onto each node
// during layout but only types the pre-layout shape publicly — this is
// the subset this file actually reads off the computed `payload`. Despite
// the names, `sourceLinks` is the *incoming* set and `targetLinks` the
// *outgoing* one (every link whose own `source` is this node) — see the
// library's own `searchTargetsAndSources`.
interface ComputedSankeyNode extends SankeyNode {
  value: number
  sourceLinks: unknown[]
}

const MIN_LABEL_HEIGHT = 13
const LABEL_FONT_SIZE = 11
const CHAR_WIDTH_ESTIMATE = LABEL_FONT_SIZE * 0.56
const LABEL_BOX_PADDING_X = 5
const LABEL_BOX_HEIGHT = 16
const MAX_LABEL_CHARS = 28
const LABEL_GAP_PX = 6

interface LabelBox {
  x0: number
  x1: number
  y0: number
  y1: number
}

// Greedy collision avoidance, checked in 2D rather than just vertically:
// nudges a new label down just far enough to clear every already-placed box
// whose *horizontal* span overlaps its own. Two labels stacked close
// together on the same side (e.g. two small nodes only `nodePadding` apart)
// share the same x-range and so still get pushed apart as before — but this
// also catches the one cross-side case that vertical-only checking missed:
// an initial (leftmost) node's label sits to its *right*, in the same gap
// its very next column's node's label sits to *its* left, so the two can
// land at a similar height and collide even though they belong to different
// node columns and were never compared against each other.
function placeWithoutOverlap(placed: LabelBox[], x0: number, x1: number, y0: number, y1: number): number {
  let shift = 0
  for (const box of placed) {
    if (x0 >= box.x1 || x1 <= box.x0) continue
    if (y0 + shift < box.y1 + 1 && y1 + shift > box.y0 - 1) {
      shift = box.y1 - y0 + 1
    }
  }
  placed.push({ x0, x1, y0: y0 + shift, y1: y1 + shift })
  return shift
}

function truncateLabel(text: string): string {
  return text.length > MAX_LABEL_CHARS ? `${text.slice(0, MAX_LABEL_CHARS - 1)}…` : text
}

// A node's name + amount, always visible (not just on hover), placed
// outside the node — to its left by default, or to its right for an
// initial (leftmost, nothing flows into it) node, so its label never runs
// past the chart's own left edge. `placedLabels` collects this render
// pass's already-placed boxes (both sides together — see
// `placeWithoutOverlap`), so a later node's label is pushed down instead of
// overlapping an earlier one, whichever side either is on.
function makeSankeyNodeLabel(displayCurrency: CurrencyCode, placedLabels: LabelBox[]) {
  return function SankeyNodeLabel(props: NodeProps) {
    const { x, y, width, height, payload } = props
    const node = payload as unknown as ComputedSankeyNode
    const rect = <rect x={x} y={y} width={width} height={height} fill={node.fill} />
    if (height < MIN_LABEL_HEIGHT) return rect

    const isInitial = node.sourceLinks.length === 0
    const text = truncateLabel(`${node.name} · ${formatCurrency(node.value, displayCurrency)}`)
    const textWidth = text.length * CHAR_WIDTH_ESTIMATE
    const boxWidth = textWidth + LABEL_BOX_PADDING_X * 2
    const centerY = y + height / 2
    const boxX = isInitial ? x + width + LABEL_GAP_PX : x - LABEL_GAP_PX - boxWidth
    const shift = placeWithoutOverlap(
      placedLabels,
      boxX,
      boxX + boxWidth,
      centerY - LABEL_BOX_HEIGHT / 2,
      centerY + LABEL_BOX_HEIGHT / 2,
    )
    const boxCenterY = centerY + shift

    return (
      <g>
        {rect}
        <rect
          x={boxX}
          y={boxCenterY - LABEL_BOX_HEIGHT / 2}
          width={boxWidth}
          height={LABEL_BOX_HEIGHT}
          rx={3}
          fill="var(--card, #fff)"
          fillOpacity={0.92}
          stroke="var(--border, #e2e8f0)"
          strokeWidth={1}
        />
        <text
          x={boxX + boxWidth / 2}
          y={boxCenterY}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={LABEL_FONT_SIZE}
          fill="#1e293b"
        >
          {text}
        </text>
      </g>
    )
  }
}

export function CashflowSankeyChart({
  categoryTotals,
  goalFlows = [],
  displayCurrency,
  title = 'Cash flow',
}: {
  categoryTotals: CategoryTotalRow[]
  goalFlows?: GoalFlow[]
  displayCurrency: CurrencyCode
  title?: string
}) {
  const { nodes, links, isBuffered } = buildSankeyData(categoryTotals, goalFlows)
  // Fresh each render (tied to this render's own `nodes`/`links`) — the
  // node label renderer mutates this in node order as Recharts calls it, so
  // a later label sees every earlier one already placed, on either side.
  const placedLabels: LabelBox[] = []
  // `useId()`'s colons aren't safe inside a CSS `url(#...)` reference
  // unescaped, so they're stripped rather than used as-is.
  const hatchPatternId = `income-buffer-hatch-${useId().replace(/:/g, '')}`

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
        link={makeSankeyLinkPath(hatchPatternId)}
        node={makeSankeyNodeLabel(displayCurrency, placedLabels)}
        nodePadding={20}
        margin={{ left: 12, right: 16, top: 8, bottom: 8 }}
      >
        <Tooltip formatter={(value) => formatCurrency(Number(value), displayCurrency)} />
      </Sankey>
    </ChartCard>
  )
}
