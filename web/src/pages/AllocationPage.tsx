import { useSearchParams } from 'react-router-dom'
import { AllocationView } from '@/components/investments/AllocationView'
import { CashOverTimeChart } from '@/components/investments/CashOverTimeChart'
import { DataQualityPanel } from '@/components/investments/DataQualityPanel'
import { LotsTable } from '@/components/investments/LotsTable'
import { TaxEnabledToggle } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

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

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Allocation" controls={<TaxEnabledToggle />} />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
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
