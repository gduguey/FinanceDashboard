import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MonthSelect } from '@/components/accounting/MonthSelect'
import { GoalDetailChart } from '@/components/goals/GoalDetailChart'
import { GoalsOverviewCharts } from '@/components/goals/GoalsOverviewCharts'
import { GoalAutomationsPanel } from '@/components/goals/GoalAutomationsPanel'
import { ManualContributionForm } from '@/components/goals/ManualContributionForm'
import { ContributionLedgerTable } from '@/components/goals/ContributionLedgerTable'
import { colorForIndex } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import { usePersistedState } from '@/hooks/usePersistedState'
import {
  useAccountingStore,
  useGoalsSummary,
  useRunRecurringAdditions,
  useRunWithdrawalAutomation,
  useSetGoals,
} from '@/hooks/useAccountingData'

type ViewMode = 'all_time' | 'per_month'

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7)
}

function monthBounds(month: string): { start: string; end: string; dayBeforeStart: string } {
  const [year, monthNum] = month.split('-').map(Number)
  const start = `${month}-01`
  const end = new Date(year, monthNum, 0).toISOString().slice(0, 10)
  const dayBeforeStart = new Date(year, monthNum - 2, new Date(year, monthNum - 1, 0).getDate()).toISOString().slice(0, 10)
  return { start, end, dayBeforeStart }
}

function GoalListSection({ goals }: { goals: Record<string, import('@/types/accounting').Goal> }) {
  const setGoals = useSetGoals()
  const goalList = Object.values(goals).sort((a, b) => a.created_at.localeCompare(b.created_at))

  function update(goalId: string, patch: Partial<import('@/types/accounting').Goal>) {
    setGoals.mutate({ ...goals, [goalId]: { ...goals[goalId], ...patch } })
  }
  function remove(goalId: string) {
    const { [goalId]: _removed, ...rest } = goals
    setGoals.mutate(rest)
  }
  function add() {
    const goalId = `goal:${Date.now()}`
    const targetDate = new Date()
    targetDate.setFullYear(targetDate.getFullYear() + 1)
    setGoals.mutate({
      ...goals,
      [goalId]: {
        goal_id: goalId,
        name: 'New goal',
        target_amount: 1000,
        target_currency: 'USD',
        target_date: targetDate.toISOString(),
        color: colorForIndex(goalList.length),
        created_at: new Date().toISOString(),
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Goals</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {goalList.map((goal) => (
          <div key={goal.goal_id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
            <span className="size-3 shrink-0 rounded-full" style={{ backgroundColor: goal.color }} />
            <Input
              className="h-7 w-40 text-sm"
              defaultValue={goal.name}
              onBlur={(event) => event.target.value !== goal.name && update(goal.goal_id, { name: event.target.value })}
            />
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              Target
              <Input
                type="number"
                className="h-7 w-28 text-xs"
                defaultValue={goal.target_amount}
                onBlur={(event) => update(goal.goal_id, { target_amount: Number(event.target.value) })}
              />
            </label>
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              By
              <Input
                type="date"
                className="h-7 w-36 text-xs"
                defaultValue={goal.target_date.slice(0, 10)}
                onBlur={(event) => update(goal.goal_id, { target_date: new Date(event.target.value).toISOString() })}
              />
            </label>
            <input
              type="color"
              className="h-7 w-8 shrink-0 cursor-pointer rounded border"
              value={goal.color}
              onChange={(event) => update(goal.goal_id, { color: event.target.value })}
            />
            <Button variant="ghost" size="icon" className="ml-auto" onClick={() => remove(goal.goal_id)}>
              <Trash2 className="size-3.5 text-muted-foreground" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={add}>
          + Add goal
        </Button>
      </CardContent>
    </Card>
  )
}

export function GoalsPage() {
  const { data: store, isLoading } = useAccountingStore()
  const [mode, setMode] = usePersistedState<ViewMode>('accounting.goals.view-mode', 'all_time')
  const [month, setMonth] = usePersistedState('accounting.goals.month', currentMonth())
  const [selectedGoalId, setSelectedGoalId] = useState<string | null>(null)
  const runRecurringAdditions = useRunRecurringAdditions()
  const runWithdrawalAutomation = useRunWithdrawalAutomation()

  // No background scheduler exists in this app — recurring additions and
  // the withdrawal automation are instead "caught up" every time this
  // page loads, which is the natural moment a user would notice a change
  // anyway (see api.post_run_recurring_additions's own docstring).
  useEffect(() => {
    runRecurringAdditions.mutate(undefined)
    runWithdrawalAutomation.mutate(undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { end, dayBeforeStart } = monthBounds(month)
  const allTimeSummary = useGoalsSummary()
  const monthEndSummary = useGoalsSummary(end)
  const monthStartSummary = useGoalsSummary(dayBeforeStart)

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
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Goals</h1>
        <div className="flex items-center gap-3">
          {unallocatedNow < 0 && (
            <span className="rounded-md bg-destructive/10 px-3 py-1 text-xs font-medium text-destructive">
              Unallocated is negative ({formatCurrency(unallocatedNow, 'USD')}) — goals couldn't fully cover a shortfall
            </span>
          )}
          <span className="text-sm text-muted-foreground">
            Unallocated: <span className="font-medium text-foreground">{formatCurrency(unallocatedNow, 'USD')}</span>
          </span>
        </div>
      </div>

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        <GoalListSection goals={store.goals} />

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
        />

        {goalList.length > 0 && (
          <Tabs value={selectedGoal?.goal_id} onValueChange={setSelectedGoalId}>
            <TabsList>
              {goalList.map((goal) => (
                <TabsTrigger key={goal.goal_id} value={goal.goal_id}>
                  {goal.name}
                </TabsTrigger>
              ))}
            </TabsList>
            {goalList.map((goal) => (
              <TabsContent key={goal.goal_id} value={goal.goal_id}>
                <GoalDetailChart goal={goal} contributions={contributionList} />
              </TabsContent>
            ))}
          </Tabs>
        )}

        <ManualContributionForm goals={store.goals} contributions={store.goal_contributions} />

        <GoalAutomationsPanel
          goals={store.goals}
          recurringAdditions={store.recurring_additions}
          withdrawalPriorities={store.withdrawal_priorities}
        />

        <ContributionLedgerTable contributions={store.goal_contributions} goals={store.goals} />
      </div>
    </div>
  )
}
