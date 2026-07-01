import { InvestmentTimelineChart } from '@/components/investments/InvestmentTimelineChart'
import { MetricsRow } from '@/components/investments/MetricsRow'
import { MonthlyInvestedChart } from '@/components/investments/MonthlyInvestedChart'
import { PortfolioBreakdownChart } from '@/components/investments/PortfolioBreakdownChart'
import { ReturnCurveChart } from '@/components/investments/ReturnCurveChart'
import { ReturnsTable } from '@/components/investments/ReturnsTable'
import { SyncButton } from '@/components/investments/SyncButton'
import { PageHeader } from '@/components/layout/PageHeader'
import { useSummary } from '@/hooks/usePortfolioData'

function SectionTitle({ children }: { children: string }) {
  return <h2 className="text-sm font-medium text-muted-foreground">{children}</h2>
}

export function InvestmentsPage() {
  const { data: summary } = useSummary()

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Investments"
        actions={<SyncButton lastSyncedAt={summary?.last_synced_at ?? null} />}
      />

      <div className="mx-auto max-w-6xl space-y-10 px-8 py-8">
        <section id="overview" className="scroll-section space-y-4">
          <SectionTitle>Overview</SectionTitle>
          <MetricsRow />
        </section>

        <section id="schedule" className="scroll-section space-y-4">
          <SectionTitle>Investment schedule</SectionTitle>
          <div className="grid gap-4 lg:grid-cols-2">
            <MonthlyInvestedChart />
            <PortfolioBreakdownChart />
          </div>
          <InvestmentTimelineChart />
        </section>

        <section id="returns" className="scroll-section space-y-4">
          <SectionTitle>Returns vs. HYSA benchmark</SectionTitle>
          <ReturnCurveChart />
          <ReturnsTable />
        </section>
      </div>
    </div>
  )
}
