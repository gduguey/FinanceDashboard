import { AlertTriangle, TrendingDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import { formatCurrency } from '@/lib/format'
import { useBudgetComparison, useGoalsSummary } from '@/hooks/useAccountingData'
import { useCashSitting } from '@/hooks/usePortfolioData'
import type { BudgetComparisonRow, CurrencyCode } from '@/types/accounting'
import type { CashSitting } from '@/types/portfolio'

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

interface Alert {
  id: string
  message: string
  href: string
  severity: 'warning' | 'critical'
}

function buildAlerts(
  cashSitting: CashSitting | undefined,
  budgetRows: BudgetComparisonRow[],
  unallocated: number | undefined,
  displayCurrency: CurrencyCode,
): Alert[] {
  const alerts: Alert[] = []

  // Guards against two distinct "nothing to say" cases the same way:
  // `cashSitting` stays `undefined` when trades has no portfolio to
  // report on at all (query error, or simply never synced) — and even
  // with a real response, a near-zero balance can't meaningfully be
  // "sitting," so it shouldn't read as a warning either.
  if (cashSitting && cashSitting.warning_level !== 'none' && cashSitting.cash_usd > 1) {
    alerts.push({
      id: 'cash-sitting',
      message: `Cash has been sitting idle for ${cashSitting.days_sitting} days — an estimated ${formatCurrency(cashSitting.missed_earnings_benchmark_usd, 'USD')} missed vs. the benchmark`,
      href: '/investments/allocation',
      severity: cashSitting.warning_level === 'heavy' ? 'critical' : 'warning',
    })
  }

  const overBudget = budgetRows
    .filter((row) => row.budgeted > 0 && row.actual > row.budgeted)
    .sort((a, b) => b.actual - b.budgeted - (a.actual - a.budgeted))
  if (overBudget.length > 0) {
    const worst = overBudget[0]
    const label = worst.subcategory_name ?? worst.category_name
    const overage = worst.actual - worst.budgeted
    const rest = overBudget.length > 1 ? ` (+${overBudget.length - 1} more)` : ''
    alerts.push({
      id: 'budget-overrun',
      message: `${label} is ${formatCurrency(overage, worst.currency)} over budget this month${rest}`,
      href: '/budget',
      severity: 'warning',
    })
  }

  // Unprompted and always shown when negative — this means a goal
  // automation or manual contribution has already allocated more than
  // there is real money to back it, not just "budget is tight."
  if (unallocated !== undefined && unallocated < 0) {
    alerts.push({
      id: 'unallocated-negative',
      message: `Unallocated goal money is negative (${formatCurrency(unallocated, displayCurrency)}) — goals have claimed more than what's actually there`,
      href: '/goals',
      severity: 'critical',
    })
  }

  return alerts
}

// The one place that pools every "you might want to look at this" signal
// this app already computes elsewhere but never surfaces together — cash
// sitting idle (Allocation page), a category running over budget (Budget
// page), goals collectively over-allocated (Goals page). None of these are
// wrong to leave on their own page, but nobody visits all three just to
// check nothing's on fire.
export function AlertsPanel({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const { data: cashSitting } = useCashSitting()
  const { data: budgetRows } = useBudgetComparison(currentMonth(), displayCurrency)
  const { data: goalsSummary } = useGoalsSummary(undefined, displayCurrency)

  const alerts = buildAlerts(cashSitting, budgetRows ?? [], goalsSummary?.unallocated, displayCurrency)
  if (alerts.length === 0) return null

  return (
    <div className="flex flex-col gap-2">
      {alerts.map((alert) => (
        <Link
          key={alert.id}
          to={alert.href}
          className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-colors hover:bg-muted/50 ${
            alert.severity === 'critical'
              ? 'border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200'
              : 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200'
          }`}
        >
          {alert.severity === 'critical' ? (
            <TrendingDown className="size-4 shrink-0" />
          ) : (
            <AlertTriangle className="size-4 shrink-0" />
          )}
          {alert.message}
        </Link>
      ))}
    </div>
  )
}
