import { GitCompare, Layers, Percent, PieChart, TrendingUp } from 'lucide-react'
import { TermCard } from '@/components/shared/TermCard'
import { GLOSSARY, type GlossaryTerm } from '@/lib/glossary'

// Grouped by theme rather than one flat alphabetical list — a wash-sale
// definition and an XIRR formula have nothing to do with each other, so
// filing them under the same undifferentiated grid read as noise. Order
// within a group puts a term next to the ones it's actually related to
// (XIRR right before its own "(provisional)" footnote, a lot's Return
// right before its Annualized version) rather than alphabetizing that
// relationship away. Mirrors the section breakdown in the Guide's own
// Investments tab and in docs/trades/glossary.md.
const SECTIONS: { title: string; icon: typeof TrendingUp; terms: GlossaryTerm[] }[] = [
  {
    title: 'Portfolio metrics',
    icon: TrendingUp,
    terms: [
      'xirr',
      'xirrProvisional',
      'twr',
      'timingGap',
      'nav',
      'growthOf100',
      'portfolioValue',
      'contributions',
      'marketGain',
      'maxDrawdown',
      'cpi',
    ],
  },
  {
    title: 'Benchmarks & counterfactuals',
    icon: GitCompare,
    terms: [
      'counterfactual',
      'benchmarkIndex',
      'benchmarkCounterfactual',
      'hysaCounterfactual',
      'cashSittingCounterfactual',
      'excessValueVsHysa',
    ],
  },
  {
    title: 'Lots & trades',
    icon: Layers,
    terms: [
      'ledger',
      'lot',
      'openLot',
      'closedLot',
      'lotTerm',
      'symbolXirr',
      'lotReturn',
      'annualizedReturn',
      'realizedGain',
      'unrealizedGain',
      'excessReturnVsHysa',
      'dripReinvestment',
    ],
  },
  {
    title: 'Allocation',
    icon: PieChart,
    terms: ['drift'],
  },
  {
    title: 'Tax',
    icon: Percent,
    terms: ['taxRegime', 'ltcg', 'washSaleFlag', 'taxToggle', 'taxOwed', 'liquidationValue'],
  },
]

// Every term used across the tooltips on Investments, spelled out in
// full, grouped by theme — for anyone who'd rather read definitions once
// than hover icon by icon. The "why" behind these ideas — the ledger,
// lots, counterfactuals — lives in the Guide's own Investments tab; this
// page stays a plain lookup, styled the same way Guide's own definitions are.
export function GlossarySection() {
  return (
    <div className="space-y-10">
      {SECTIONS.map(({ title, icon: Icon, terms }) => (
        <section key={title} className="space-y-4">
          <h2 className="flex items-center gap-2.5 text-lg font-semibold tracking-tight text-foreground">
            <Icon className="size-5 text-muted-foreground" />
            {title}
          </h2>
          <dl className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {terms.map((key) => (
              <TermCard key={key} term={GLOSSARY[key].title}>
                {GLOSSARY[key].body}
              </TermCard>
            ))}
          </dl>
        </section>
      ))}
    </div>
  )
}
