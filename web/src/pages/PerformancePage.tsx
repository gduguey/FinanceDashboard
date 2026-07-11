import { BenchmarkPicker } from '@/components/investments/BenchmarkPicker'
import { CashSittingCard } from '@/components/investments/CashSittingCard'
import { DollarChart } from '@/components/investments/DollarChart'
import { GrowthOf100Chart } from '@/components/investments/GrowthOf100Chart'
import { HysaSettingsPanel } from '@/components/investments/HysaSettingsPanel'
import { InvestmentsEmptyState } from '@/components/investments/InvestmentsEmptyState'
import { MonthlyPnlChart } from '@/components/investments/MonthlyPnlChart'
import { OverviewCards } from '@/components/investments/OverviewCards'
import { SyncButton } from '@/components/investments/SyncButton'
import { TaxEnabledToggle } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { useOverview } from '@/hooks/usePortfolioData'

// Was the "Dashboard" tab's Overview + Performance sections; Allocation
// moved to its own page (`AllocationPage`), and once it did there was no
// longer more than one view left here worth tabbing between, so this is
// just one page, not a `Tabs` wrapper around what used to be sections.
export function PerformancePage() {
  const { data: overview, isError } = useOverview()
  const hasData = Boolean(overview) && !isError

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Performance"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
        controls={hasData ? <TaxEnabledToggle /> : undefined}
      />

      <div className="mx-auto max-w-6xl space-y-10 px-8 py-8">
        <InvestmentsEmptyState />

        <OverviewCards />

        <CashSittingCard />

        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-4">
            <BenchmarkPicker />
            <HysaSettingsPanel />
          </div>
          <DollarChart />
          <GrowthOf100Chart />
          <MonthlyPnlChart />
        </div>
      </div>
    </div>
  )
}
