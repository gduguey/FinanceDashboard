import { AllocationView } from '@/components/investments/AllocationView'
import { DataQualityPanel } from '@/components/investments/DataQualityPanel'
import { DollarChart } from '@/components/investments/DollarChart'
import { GrowthOf100Chart } from '@/components/investments/GrowthOf100Chart'
import { LotsTable } from '@/components/investments/LotsTable'
import { MonthlyPnlChart } from '@/components/investments/MonthlyPnlChart'
import { OverviewCards } from '@/components/investments/OverviewCards'
import { RiskStat } from '@/components/investments/RiskStat'
import { SyncButton } from '@/components/investments/SyncButton'
import { PageHeader } from '@/components/layout/PageHeader'
import { useOverview } from '@/hooks/usePortfolioData'

function SectionTitle({ children }: { children: string }) {
  return <h2 className="text-sm font-medium text-muted-foreground">{children}</h2>
}

export function InvestmentsPage() {
  const { data: overview } = useOverview()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Investments"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
      />

      <div className="mx-auto max-w-6xl space-y-10 px-8 py-8">
        <section id="overview" className="scroll-section space-y-4">
          <SectionTitle>Overview</SectionTitle>
          <OverviewCards />
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <RiskStat />
          </div>
        </section>

        <section id="performance" className="scroll-section space-y-4">
          <SectionTitle>Performance vs. benchmarks</SectionTitle>
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

        <section id="data-quality" className="scroll-section space-y-4">
          <SectionTitle>Trust &amp; data quality</SectionTitle>
          <DataQualityPanel />
        </section>
      </div>
    </div>
  )
}
