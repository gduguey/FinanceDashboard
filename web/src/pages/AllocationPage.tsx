import { useSearchParams } from 'react-router-dom'
import { DataQualityPanel } from '@/components/investments/DataQualityPanel'
import { InvestmentsEmptyState } from '@/components/investments/InvestmentsEmptyState'
import { LotsTable } from '@/components/investments/LotsTable'
import { SyncButton } from '@/components/investments/SyncButton'
import { TaxEnabledToggle } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { lazyChart } from '@/components/shared/lazyChart'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useOverview } from '@/hooks/usePortfolioData'

// Charts are the heaviest code the app ships. `lazyChart` keeps them off this
// page's critical path and streams them in behind a skeleton.
const AllocationView = lazyChart(() => import('@/components/investments/AllocationView').then((m) => m.AllocationView))
const CashOverTimeChart = lazyChart(() =>
  import('@/components/investments/CashOverTimeChart').then((m) => m.CashOverTimeChart),
)

// Was the "Allocation" tab on the old combined Investments dashboard,
// promoted to its own page. Keeps the tax toggle in its header (unlike
// Glossary) since the Lots table's own tax-lot detail changes shape
// depending on it, same as Performance's after-tax figures do.
// Data quality (last price sync per symbol) lives here too, next to Lots,
// since that's the one place per-symbol data actually gets used.
export function AllocationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'overview'
  const setTab = (value: string) => setSearchParams(value === 'overview' ? {} : { tab: value })
  const { data: overview, isError } = useOverview()
  const hasData = Boolean(overview) && !isError

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Allocation"
        actions={<SyncButton lastSyncedAt={overview?.last_synced_at ?? null} />}
        controls={hasData ? <TaxEnabledToggle /> : undefined}
      />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
        <InvestmentsEmptyState />

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="lots">Lots</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="space-y-6 pt-2">
            <AllocationView />
            <CashOverTimeChart />
          </TabsContent>
          <TabsContent value="lots" className="space-y-6 pt-2">
            <LotsTable />
            <DataQualityPanel />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
