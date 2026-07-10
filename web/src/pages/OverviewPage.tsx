import { useMemo } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { NoAccountsYetBanner } from '@/components/shared/NoAccountsYetBanner'
import { PageHeader } from '@/components/layout/PageHeader'
import { CashflowSankeyChart, type GoalFlow } from '@/components/accounting/CashflowSankeyChart'
import { NetWorthAllocationPie } from '@/components/accounting/NetWorthAllocationPie'
import { GoalsBalanceBarChart } from '@/components/goals/GoalsOverviewCharts'
import { AlertsPanel } from '@/components/overview/AlertsPanel'
import { FinancialHealthStrip } from '@/components/overview/FinancialHealthStrip'
import { SyncStatusBar } from '@/components/overview/SyncStatusBar'
import { WhatChangedCard } from '@/components/overview/WhatChangedCard'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import {
  useAccountingStore,
  useCategoryTotals,
  useCurrencies,
  useGoalsSummary,
  useNetWorth,
  useRatesToBase,
} from '@/hooks/useAccountingData'
import { useMonthlyPnl } from '@/hooks/usePortfolioData'

// A color of its own — distinct from every goal's own color, from "Saved"
// (emerald), and from "Unallocated" (pale green) — so money that left
// this month's cash for the brokerage reads as its own destination, not a
// shade of either.
const INVESTED_COLOR = '#6366f1'

function currentMonthBounds(): { start: string; end: string } {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().slice(0, 10)
  return { start, end }
}

function currentMonthString(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

// The app's own front door — a handful of already-built charts pulled
// from Net Worth, Budget, Goals, and Investments, so the very first thing
// a user sees is a bird's-eye view rather than having to pick a page
// first, and any warning already computed somewhere else in the app
// (cash sitting idle, a budget running hot, goals over-allocated) shows up
// here too, unprompted, instead of only on the page that owns it.
export function OverviewPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data: store, isLoading } = useAccountingStore()
  const { start, end } = currentMonthBounds()
  const month = currentMonthString()
  const { data: categoryTotals } = useCategoryTotals(start, end, undefined, undefined, displayCurrency)
  const goalsSummary = useGoalsSummary(undefined, displayCurrency)
  const { data: monthlyPnl } = useMonthlyPnl()
  const { data: netWorth } = useNetWorth(undefined, displayCurrency)
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD')
  const ratesToBase = useRatesToBase(nonBaseCurrencies)

  // Same per-goal totals `DashboardTab` computes for the user-selected
  // period filter, just fixed to this calendar month — plus one more flow,
  // "Invested," that neither Money's own cashflow view nor Investments'
  // own contribution chart shows on its own: money that left checking for
  // the brokerage this month is a transfer in accounting's own ledger (see
  // `AccountKind.external_investment`), so it never appears as an expense
  // category here at all without this.
  const goalFlows: GoalFlow[] = useMemo(() => {
    const totals = new Map<string, number>()
    for (const contribution of Object.values(store?.goal_contributions ?? {})) {
      const day = contribution.date.slice(0, 10)
      if (day < start || day > end || contribution.amount <= 0) continue
      totals.set(contribution.goal_id, (totals.get(contribution.goal_id) ?? 0) + contribution.amount)
    }
    const flows = [...totals.entries()]
      .map(([goalId, value]) => ({
        name: store?.goals[goalId]?.name ?? goalId,
        value,
        color: store?.goals[goalId]?.color ?? '#059669',
      }))
      .filter((flow) => flow.value > 0)

    const investedThisMonth = monthlyPnl?.find((row) => row.month === month)?.contributions_usd ?? 0
    if (investedThisMonth > 0) {
      flows.push({ name: 'Invested', value: investedThisMonth, color: INVESTED_COLOR })
    }
    return flows
  }, [store?.goal_contributions, store?.goals, monthlyPnl, month, start, end])

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Overview"
        actions={
          <>
            <DisplayCurrencyToggle />
            <ExchangeRateSyncButton />
          </>
        }
      />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        <SyncStatusBar />

        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            {!hasAnyRealAccount(Object.values(store.accounts)) && <NoAccountsYetBanner />}
            <AlertsPanel displayCurrency={displayCurrency} />
            <FinancialHealthStrip displayCurrency={displayCurrency} />
            <WhatChangedCard displayCurrency={displayCurrency} />
            <CashflowSankeyChart
              categoryTotals={categoryTotals ?? []}
              goalFlows={goalFlows}
              displayCurrency={displayCurrency}
              title="This month's cash flow — spent, saved, or invested"
            />
            {Object.keys(store.goals).length > 0 ? (
              <div className="grid gap-4 lg:grid-cols-2">
                <NetWorthAllocationPie
                  accounts={netWorth?.accounts ?? []}
                  otherAssets={netWorth?.other_assets ?? []}
                  displayCurrency={displayCurrency}
                  ratesToBase={ratesToBase}
                />
                <GoalsBalanceBarChart
                  goals={Object.values(store.goals)}
                  balances={goalsSummary.data?.balances ?? {}}
                  targets={Object.fromEntries(
                    Object.values(store.goals).map((goal) => [goal.goal_id, goal.target_amount]),
                  )}
                  mode="all_time"
                  displayCurrency={displayCurrency}
                />
              </div>
            ) : (
              <NetWorthAllocationPie
                accounts={netWorth?.accounts ?? []}
                otherAssets={netWorth?.other_assets ?? []}
                displayCurrency={displayCurrency}
                ratesToBase={ratesToBase}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
