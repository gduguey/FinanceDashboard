import { DataQualityPanel } from '@/components/investments/DataQualityPanel'
import { GlossarySection } from '@/components/investments/GlossarySection'
import { PageHeader } from '@/components/layout/PageHeader'

// Static reference material — trust/data-quality notes and term
// definitions — deliberately its own page, not a tab shared with the live
// dashboard: neither one is "this period's numbers", so mixing them into
// the same view read as incoherent with the rest of the app, where a live
// view and its own reference material are never on the same page.
export function InvestmentsReferencePage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Reference" />

      <div className="mx-auto max-w-6xl space-y-10 px-8 py-8">
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-muted-foreground">Trust &amp; data quality</h2>
          <DataQualityPanel />
        </section>

        <section className="space-y-4">
          <h2 className="text-sm font-medium text-muted-foreground">Glossary</h2>
          <GlossarySection />
        </section>
      </div>
    </div>
  )
}
