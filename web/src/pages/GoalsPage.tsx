import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'
import { MonthSelect } from '@/components/accounting/MonthSelect'
import { ContributionLedgerTable } from '@/components/goals/ContributionLedgerTable'
import { GoalAutomationsPanel } from '@/components/goals/GoalAutomationsPanel'
import { GoalDetailChart } from '@/components/goals/GoalDetailChart'
import { GoalsOverviewCharts } from '@/components/goals/GoalsOverviewCharts'
import { PageHeader } from '@/components/layout/PageHeader'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useAccountingStore,
  useCreateGoal,
  useCurrencies,
  useDeleteGoal,
  useGoalsSummary,
  usePatchGoal,
  useRunRecurringAdditions,
  useRunWithdrawalAutomation,
} from '@/hooks/useAccountingData'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { usePersistedState } from '@/hooks/usePersistedState'
import { formatCurrency, formatDate } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

type ViewMode = 'all_time' | 'per_month'

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7)
}

function monthBounds(month: string): { start: string; end: string; dayBeforeStart: string } {
  const [year, monthNum] = month.split('-').map(Number)
  const start = `${month}-01`
  const end = new Date(year, monthNum, 0).toISOString().slice(0, 10)
  const dayBeforeStart = new Date(year, monthNum - 2, new Date(year, monthNum - 1, 0).getDate())
    .toISOString()
    .slice(0, 10)
  return { start, end, dayBeforeStart }
}

function GoalListSection({
  goals,
  defaultCurrency,
  selectedGoalId,
  onSelectGoal,
}: {
  goals: Record<string, import('@/types/accounting').Goal>
  defaultCurrency: CurrencyCode
  // Doubles as "which row is expanded for inline editing" — a click both
  // selects the goal (for the detail chart below) and opens its edit
  // fields, so a master-detail list and a table's own row editor don't
  // need two separate pieces of selection state.
  selectedGoalId: string | null
  onSelectGoal: (goalId: string | null) => void
}) {
  const patchGoal = usePatchGoal()
  const deleteGoal = useDeleteGoal()
  const createGoal = useCreateGoal()
  const { data: currencies } = useCurrencies()
  const currencyItems = Object.fromEntries((currencies ?? []).map((currency) => [currency.code, currency.code]))
  const goalList = Object.values(goals).sort((a, b) => a.created_at.localeCompare(b.created_at))

  function update(goalId: string, patch: Partial<import('@/types/accounting').Goal>) {
    const goal = goals[goalId]
    const merged = { ...goal, ...patch }
    patchGoal.mutate({
      goalId,
      update: {
        name: merged.name,
        target_amount: merged.target_amount,
        target_currency: merged.target_currency,
        target_date: merged.target_date,
        color: merged.color,
        expected_version: goal.version,
      },
    })
  }
  function remove(goalId: string) {
    deleteGoal.mutate(goalId)
    if (selectedGoalId === goalId) onSelectGoal(null)
  }
  async function add() {
    const targetDate = new Date()
    targetDate.setFullYear(targetDate.getFullYear() + 1)
    const goal = await createGoal.mutateAsync({
      name: 'New goal',
      target_amount: 1000,
      target_currency: defaultCurrency,
      target_date: targetDate.toISOString(),
    })
    onSelectGoal(goal.goal_id)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Goals</CardTitle>
        <CardAction>
          <Button variant="outline" size="icon" onClick={add} title="Add goal">
            <Plus className="size-4" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {goalList.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">No goals yet — add one to get started.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-6" />
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Target</TableHead>
                <TableHead>By</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {goalList.map((goal) => {
                const expanded = selectedGoalId === goal.goal_id
                return (
                  <Fragment key={goal.goal_id}>
                    <TableRow
                      className={`cursor-pointer ${expanded ? 'bg-muted/40' : ''}`}
                      onClick={() => onSelectGoal(expanded ? null : goal.goal_id)}
                    >
                      <TableCell>
                        {expanded ? (
                          <ChevronDown className="size-3.5 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="size-3.5 text-muted-foreground" />
                        )}
                      </TableCell>
                      <TableCell className="font-medium">
                        <span className="flex items-center gap-2">
                          <span className="size-3 shrink-0 rounded-full" style={{ backgroundColor: goal.color }} />
                          {goal.name}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCurrency(goal.target_amount, goal.target_currency)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDate(goal.target_date)}</TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={(event) => {
                            event.stopPropagation()
                            remove(goal.goal_id)
                          }}
                        >
                          <Trash2 className="size-3.5 text-muted-foreground" />
                        </Button>
                      </TableCell>
                    </TableRow>
                    {expanded && (
                      <TableRow className="bg-muted/30 hover:bg-muted/30">
                        <TableCell colSpan={5}>
                          <div
                            className="flex flex-wrap items-end gap-3 py-1"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                              Name
                              <Input
                                className="h-7 w-40 text-sm"
                                defaultValue={goal.name}
                                onBlur={(event) =>
                                  event.target.value !== goal.name && update(goal.goal_id, { name: event.target.value })
                                }
                              />
                            </label>
                            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                              Target amount
                              <NumberInput
                                className="h-7 w-28 text-xs"
                                value={goal.target_amount}
                                onCommit={(value) => update(goal.goal_id, { target_amount: value ?? 0 })}
                              />
                            </label>
                            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                              Currency
                              <Select
                                value={goal.target_currency}
                                onValueChange={(value) =>
                                  value && update(goal.goal_id, { target_currency: value as CurrencyCode })
                                }
                              >
                                <SelectTrigger size="sm" className="h-7 w-20 text-xs">
                                  <SelectValue items={currencyItems} />
                                </SelectTrigger>
                                <SelectContent>
                                  {(currencies ?? []).map((currency) => (
                                    <SelectItem key={currency.code} value={currency.code}>
                                      {currency.code}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </label>
                            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                              Target date
                              <Input
                                type="date"
                                className="h-7 w-36 text-xs"
                                defaultValue={goal.target_date.slice(0, 10)}
                                onBlur={(event) =>
                                  update(goal.goal_id, { target_date: new Date(event.target.value).toISOString() })
                                }
                              />
                            </label>
                            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                              Color
                              <input
                                type="color"
                                className="h-7 w-12 cursor-pointer rounded border"
                                value={goal.color}
                                onChange={(event) => update(goal.goal_id, { color: event.target.value })}
                              />
                            </label>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export function GoalsPage() {
  const { data: store, isLoading } = useAccountingStore()
  const { displayCurrency } = useDisplayCurrency()
  const [mode, setMode] = usePersistedState<ViewMode>('accounting.goals.view-mode', 'all_time')
  const [month, setMonth] = usePersistedState('accounting.goals.month', currentMonth())
  const [pageTab, setPageTab] = usePersistedState('accounting.goals.page-tab', 'overview')
  const [selectedGoalId, setSelectedGoalId] = useState<string | null>(null)
  const runRecurringAdditions = useRunRecurringAdditions()
  const runWithdrawalAutomation = useRunWithdrawalAutomation()

  // No background scheduler exists in this app — recurring additions and
  // the withdrawal automation are instead "caught up" every time this
  // page loads, which is the natural moment a user would notice a change
  // anyway (see api.post_run_recurring_additions's own docstring). Run in
  // sequence, not fired together: each request snapshots the last-known
  // store version, which only advances once its own mutation's `onSuccess`
  // invalidation has refetched the store — firing both at once would have
  // the second spuriously 409 against the version the first just bumped.
  // Sequencing also means the withdrawal check sees whatever the recurring
  // addition just contributed, not a stale pre-addition balance.
  useEffect(() => {
    async function catchUpAutomations() {
      await runRecurringAdditions.mutateAsync(undefined)
      await runWithdrawalAutomation.mutateAsync(undefined)
    }
    catchUpAutomations()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { end, dayBeforeStart } = monthBounds(month)
  const allTimeSummary = useGoalsSummary(undefined, displayCurrency)
  const monthEndSummary = useGoalsSummary(end, displayCurrency)
  const monthStartSummary = useGoalsSummary(dayBeforeStart, displayCurrency)

  if (isLoading || !store) {
    return (
      <div className="flex-1 overflow-y-auto p-8">
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const goalList = Object.values(store.goals)
  const contributionList = Object.values(store.goal_contributions)
  const selectedGoal = goalList.find((g) => g.goal_id === selectedGoalId) ?? goalList[0] ?? null

  const stockBalances = allTimeSummary.data?.balances ?? {}
  const stockUnallocated = allTimeSummary.data?.unallocated ?? 0
  const flowBalances = Object.fromEntries(
    goalList.map((goal) => [
      goal.goal_id,
      (monthEndSummary.data?.balances[goal.goal_id] ?? 0) - (monthStartSummary.data?.balances[goal.goal_id] ?? 0),
    ]),
  )
  const flowUnallocated = (monthEndSummary.data?.unallocated ?? 0) - (monthStartSummary.data?.unallocated ?? 0)

  const allTimeTargets = Object.fromEntries(goalList.map((goal) => [goal.goal_id, goal.target_amount]))
  // Per-month "target": the amount that would need to go in this month to
  // stay on a linear pace from the goal's first-ever contribution to its
  // target date — see GoalsOverviewCharts's own docstring reference.
  const monthlyLinearTargets = Object.fromEntries(
    goalList.map((goal) => {
      const goalContributions = contributionList.filter((c) => c.goal_id === goal.goal_id)
      if (goalContributions.length === 0) return [goal.goal_id, 0]
      const firstDate = new Date(Math.min(...goalContributions.map((c) => new Date(c.date).getTime())))
      const targetDate = new Date(goal.target_date)
      const totalMonths = Math.max(
        1,
        (targetDate.getFullYear() - firstDate.getFullYear()) * 12 + (targetDate.getMonth() - firstDate.getMonth()),
      )
      return [goal.goal_id, goal.target_amount / totalMonths]
    }),
  )

  const unallocatedNow = allTimeSummary.data?.unallocated ?? 0

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Goals"
        actions={
          <>
            {unallocatedNow < 0 && (
              <span className="rounded-md bg-destructive/10 px-3 py-1 text-xs font-medium text-destructive">
                Unallocated is negative ({formatCurrency(unallocatedNow, displayCurrency)}) — goals couldn't fully cover
                a shortfall
              </span>
            )}
            <span className="text-sm text-muted-foreground">
              Unallocated:{' '}
              <span className="font-medium text-foreground">{formatCurrency(unallocatedNow, displayCurrency)}</span>
            </span>
            <DisplayCurrencyToggle />
          </>
        }
      />

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        <Tabs value={pageTab} onValueChange={setPageTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="automations">Automations</TabsTrigger>
            <TabsTrigger value="ledger">Contribution ledger</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="space-y-6">
            <GoalListSection
              goals={store.goals}
              defaultCurrency={displayCurrency}
              selectedGoalId={selectedGoalId}
              onSelectGoal={setSelectedGoalId}
            />

            {selectedGoal && <GoalDetailChart goal={selectedGoal} contributions={contributionList} />}

            <div className="flex items-center gap-3">
              <Select value={mode} onValueChange={(value) => value && setMode(value as ViewMode)}>
                <SelectTrigger size="sm" className="min-w-40">
                  <SelectValue items={{ all_time: 'All-time (balances)', per_month: 'Per-month (flow)' }} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all_time">All-time (balances)</SelectItem>
                  <SelectItem value="per_month">Per-month (flow)</SelectItem>
                </SelectContent>
              </Select>
              {mode === 'per_month' && (
                <MonthSelect
                  value={month}
                  onChange={setMonth}
                  months={[...new Set(contributionList.map((c) => c.date.slice(0, 7)))].sort().reverse()}
                />
              )}
            </div>

            <GoalsOverviewCharts
              goals={goalList}
              balances={mode === 'all_time' ? stockBalances : flowBalances}
              unallocated={mode === 'all_time' ? stockUnallocated : flowUnallocated}
              targets={mode === 'all_time' ? allTimeTargets : monthlyLinearTargets}
              mode={mode}
              displayCurrency={displayCurrency}
            />
          </TabsContent>
          <TabsContent value="automations">
            <GoalAutomationsPanel
              goals={store.goals}
              recurringAdditions={store.recurring_additions}
              withdrawalPriorities={store.withdrawal_priorities}
            />
          </TabsContent>
          <TabsContent value="ledger">
            <ContributionLedgerTable contributions={store.goal_contributions} goals={store.goals} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
