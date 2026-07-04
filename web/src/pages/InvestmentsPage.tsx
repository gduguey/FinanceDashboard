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
import { PageHeader } from '@/components/layout/PageHeader'
import { useOverview, useTaxSettings } from '@/hooks/usePortfolioData'

function SectionTitle({ children }: { children: string }) {
  return <h2 className="text-sm font-medium text-muted-foreground">{children}</h2>
}

export function InvestmentsPage() {
  const { data: overview } = useOverview()
  const { data: taxSettings } = useTaxSettings()
  const taxEnabled = taxSettings?.tax_enabled ?? false

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Investments"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
        controls={<TaxSettingsControls />}
        taxEnabled={taxEnabled}
      />

      <div className="mx-auto max-w-6xl space-y-10 px-8 py-8">
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

        <section id="taxes" className="scroll-section space-y-4">
          <TaxDetailSection />
        </section>

        <section id="data-quality" className="scroll-section space-y-4">
          <SectionTitle>Trust &amp; data quality</SectionTitle>
          <DataQualityPanel />
        </section>

        <section id="glossary" className="scroll-section space-y-4">
          <SectionTitle>Glossary</SectionTitle>
          <GlossarySection />
        </section>
      </div>
    </div>
  )
}
