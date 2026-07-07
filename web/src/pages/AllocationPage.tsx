import { AllocationView } from '@/components/investments/AllocationView'
import { CashOverTimeChart } from '@/components/investments/CashOverTimeChart'
import { LotsTable } from '@/components/investments/LotsTable'
import { TaxEnabledToggle } from '@/components/investments/TaxPanel'
import { PageHeader } from '@/components/layout/PageHeader'

// Was the "Allocation" tab on the old combined Investments dashboard,
// promoted to its own page. Keeps the tax toggle in its header (unlike
// Reference) since the Lots table's own tax-lot detail changes shape
// depending on it, same as Performance's after-tax figures do.
export function AllocationPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Allocation" controls={<TaxEnabledToggle />} />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
        <AllocationView />
        <CashOverTimeChart />
        <LotsTable />
      </div>
    </div>
  )
}
