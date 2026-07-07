import { TaxDetailSection, TaxSettingsControls } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { useTaxSettings } from '@/hooks/usePortfolioData'

// Was a conditionally-shown section at the bottom of the Investments
// dashboard, promoted to its own top-level page — a full annual report is
// its own destination, not a scroll-stop on the live dashboard. The
// enable/regime toggle is repeated here (as well as on Investments, where
// it also controls the dashboard's after-tax figures) so turning tax
// tracking on doesn't require leaving this page first.
export function TaxesPage() {
  const { data: settings, isLoading } = useTaxSettings()
  const taxEnabled = settings?.tax_enabled ?? false

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Taxes" controls={<TaxSettingsControls />} />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        {!isLoading && !taxEnabled ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            Tax tracking is off — turn on "Apply taxes" above to see your annual report here.
          </p>
        ) : (
          <TaxDetailSection />
        )}
      </div>
    </div>
  )
}
