import { useSearchParams } from 'react-router-dom'
import { InvestmentsEmptyState } from '@/components/investments/InvestmentsEmptyState'
import { RulesCard, TaxRegimeSelector, TaxReportTab } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useTaxSettings } from '@/hooks/usePortfolioData'

// Was a conditionally-shown section at the bottom of the old Investments
// dashboard, promoted to its own top-level page. Always shows its report
// regardless of the Performance/Allocation on/off toggle (removed here
// entirely) — the backend computes it unconditionally off the resolved
// regime, so there's nothing to gate. The regime/rate selector lives only
// here, above the tab selector, since picking a regime is specific to this
// page's own report.
export function TaxesPage() {
  const { data: settings, isLoading } = useTaxSettings()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'report'
  const setTab = (value: string) => setSearchParams(value === 'report' ? {} : { tab: value })

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Taxes" />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        <InvestmentsEmptyState />

        <TaxRegimeSelector />

        {isLoading || !settings ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="report">Report</TabsTrigger>
              <TabsTrigger value="how-it-works">How it's taxed</TabsTrigger>
            </TabsList>
            <TabsContent value="report" className="pt-2">
              <TaxReportTab />
            </TabsContent>
            <TabsContent value="how-it-works" className="pt-2">
              <RulesCard
                regime={settings.tax_regime ?? settings.resolved_tax_regime}
                w8benClaimed={settings.w8ben_claimed}
              />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}
