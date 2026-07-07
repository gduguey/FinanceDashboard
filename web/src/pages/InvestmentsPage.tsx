import { useState } from 'react'
import { AllocationView } from '@/components/investments/AllocationView'
import { BenchmarkPicker } from '@/components/investments/BenchmarkPicker'
import { DataQualityPanel } from '@/components/investments/DataQualityPanel'
import { DollarChart } from '@/components/investments/DollarChart'
import { GlossarySection } from '@/components/investments/GlossarySection'
import { GrowthOf100Chart } from '@/components/investments/GrowthOf100Chart'
import { HysaSettingsPanel } from '@/components/investments/HysaSettingsPanel'
import { LotsTable } from '@/components/investments/LotsTable'
import { MonthlyPnlChart } from '@/components/investments/MonthlyPnlChart'
import { OverviewCards } from '@/components/investments/OverviewCards'
import { SyncButton } from '@/components/investments/SyncButton'
import { TaxControlBar, TaxDetailSection, TaxSettingsControls } from '@/components/investments/TaxPanel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeader, type PageHeaderSection } from '@/components/layout/PageHeader'
import { useOverview, useTaxSettings } from '@/hooks/usePortfolioData'

function SectionTitle({ children }: { children: string }) {
  return <h2 className="text-sm font-medium text-muted-foreground">{children}</h2>
}

// Tagged `tab: 'dashboard'` — these anchors only exist while the Dashboard
// tab is mounted, so `PageHeader` hides them the moment Reference is
// active instead of leaving dead links in the header (see `PageHeader`'s
// own module doc for why this is tagged rather than left implicit).
const DASHBOARD_SECTIONS: PageHeaderSection[] = [
  { id: 'overview', label: 'Overview', tab: 'dashboard' },
  { id: 'performance', label: 'Performance', tab: 'dashboard' },
  { id: 'allocation', label: 'Allocation', tab: 'dashboard' },
  { id: 'lots', label: 'Lots', tab: 'dashboard' },
]
const TAXES_SECTION: PageHeaderSection = { id: 'taxes', label: 'Taxes', tab: 'dashboard' }

export function InvestmentsPage() {
  const { data: overview } = useOverview()
  const { data: taxSettings } = useTaxSettings()
  const taxEnabled = taxSettings?.tax_enabled ?? false
  const sections = taxEnabled ? [...DASHBOARD_SECTIONS, TAXES_SECTION] : DASHBOARD_SECTIONS
  const [tab, setTab] = useState('dashboard')

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Investments"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
        controls={<TaxSettingsControls />}
        sections={sections}
        activeTab={tab}
      />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
            <TabsTrigger value="reference">Reference</TabsTrigger>
          </TabsList>
          <TabsContent value="dashboard" className="space-y-10 pt-2">
            <TaxControlBar />

            <section id="overview" className="scroll-section space-y-4">
              <SectionTitle>Overview</SectionTitle>
              <OverviewCards />
            </section>

            <section id="performance" className="scroll-section space-y-4">
              <SectionTitle>Performance vs. benchmarks</SectionTitle>
              <div className="flex flex-wrap items-center gap-4">
                <BenchmarkPicker />
                <HysaSettingsPanel />
              </div>
              <DollarChart />
              <GrowthOf100Chart />
              <MonthlyPnlChart />
            </section>

            <section id="allocation" className="scroll-section space-y-4">
              <SectionTitle>Allocation</SectionTitle>
              <AllocationView />
            </section>

            <section id="lots" className="scroll-section space-y-4">
              <SectionTitle>Lots</SectionTitle>
              <LotsTable />
            </section>

            {taxEnabled && (
              <section id="taxes" className="scroll-section space-y-4">
                <TaxDetailSection />
              </section>
            )}
          </TabsContent>
          <TabsContent value="reference" className="space-y-10 pt-2">
            {/* Static reference material — trust/data-quality notes and
                term definitions — deliberately split from the live
                dashboard tab above: neither one is "this period's
                numbers", so mixing them into the same long scroll read as
                incoherent with the rest of the app, where a live view and
                its own reference material are never on the same page. */}
            <section className="space-y-4">
              <SectionTitle>Trust &amp; data quality</SectionTitle>
              <DataQualityPanel />
            </section>

            <section className="space-y-4">
              <SectionTitle>Glossary</SectionTitle>
              <GlossarySection />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
