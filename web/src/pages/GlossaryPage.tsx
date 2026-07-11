import { GlossarySection } from '@/components/investments/GlossarySection'
import { PageHeader } from '@/components/layout/PageHeader'

// A plain term lookup — every word a tooltip anywhere on Investments
// points at, spelled out in full, alphabetical. The "why" behind these
// ideas lives in the Guide's own Investments tab; data quality (last
// price sync per symbol) moved to Allocation's Lots tab, next to the
// per-symbol data it's actually about.
export function GlossaryPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Glossary" />

      <div className="mx-auto max-w-6xl px-8 py-8">
        <GlossarySection />
      </div>
    </div>
  )
}
