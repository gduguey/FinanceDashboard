import { useSearchParams } from 'react-router-dom'
import { AllocationView } from '@/components/investments/AllocationView'
import { BenchmarkPicker } from '@/components/investments/BenchmarkPicker'
import { DollarChart } from '@/components/investments/DollarChart'
import { GrowthOf100Chart } from '@/components/investments/GrowthOf100Chart'
import { HysaSettingsPanel } from '@/components/investments/HysaSettingsPanel'
import { LotsTable } from '@/components/investments/LotsTable'
import { MonthlyPnlChart } from '@/components/investments/MonthlyPnlChart'
import { OverviewCards } from '@/components/investments/OverviewCards'
import { SyncButton } from '@/components/investments/SyncButton'
import { TaxSettingsControls } from '@/components/investments/TaxPanel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeader } from '@/components/layout/PageHeader'
import { useOverview } from '@/hooks/usePortfolioData'

// Reference (data quality + glossary) and Taxes are their own top-level
// pages now (see `InvestmentsReferencePage`/`TaxesPage`) — this page is
// just the live dashboard, so its own three tabs stay real tabs rather
// than anchor-nav sections sharing a page with unrelated material.
export function InvestmentsPage() {
  const { data: overview } = useOverview()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'overview'
  const setTab = (value: string) => setSearchParams(value === 'overview' ? {} : { tab: value })

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Investments"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
        controls={<TaxSettingsControls />}
      />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="allocation">Allocation</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-4 pt-2">
            <OverviewCards />
          </TabsContent>

          <TabsContent value="performance" className="space-y-4 pt-2">
            <div className="flex flex-wrap items-center gap-4">
              <BenchmarkPicker />
              <HysaSettingsPanel />
            </div>
            <DollarChart />
            <GrowthOf100Chart />
            <MonthlyPnlChart />
          </TabsContent>

          <TabsContent value="allocation" className="space-y-6 pt-2">
            <AllocationView />
            <LotsTable />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
