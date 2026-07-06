import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { formatCurrency } from '@/lib/format'
import { useSetGoalContributions, useSimulateContribution } from '@/hooks/useAccountingData'
import type { Goal, GoalContribution } from '@/types/accounting'

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10)
}

// A manual contribution is validated against the running unallocated
// total *as of the date entered* (not today's) — a backdated entry can't
// be slipped in just because today's balance happens to cover it. The
// "would this push a future automation run negative" check is
// non-blocking, per spec — a warning, never a hard stop.
export function ManualContributionForm({
  goals,
  contributions,
}: {
  goals: Record<string, Goal>
  contributions: Record<string, GoalContribution>
}) {
  const goalList = Object.values(goals).sort((a, b) => a.name.localeCompare(b.name))
  const [goalId, setGoalId] = useState<string | null>(goalList[0]?.goal_id ?? null)
  const [date, setDate] = useState(todayIsoDate())
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const setContributions = useSetGoalContributions()
  const simulate = useSimulateContribution()

  const parsedAmount = Number.parseFloat(amount) || 0

  async function handleCheck() {
    if (!goalId || parsedAmount === 0) return
    simulate.mutate({ goalId, date, amount: parsedAmount })
  }

  async function handleSubmit() {
    if (!goalId || parsedAmount === 0) return
    const contributionId = `manual:${Date.now()}`
    const contribution: GoalContribution = {
      contribution_id: contributionId,
      goal_id: goalId,
      date: new Date(date).toISOString(),
      amount: parsedAmount,
      currency: 'USD',
      note,
      source_posting_id: null,
      origin: 'manual',
      edited: false,
    }
    await setContributions.mutateAsync({ ...contributions, [contributionId]: contribution })
    setAmount('')
    setNote('')
    simulate.reset()
  }

  const result = simulate.data

  if (goalList.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a contribution</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Goal
            <Select value={goalId ?? undefined} onValueChange={(value) => setGoalId(value)}>
              <SelectTrigger size="sm" className="min-w-40">
                <SelectValue items={Object.fromEntries(goalList.map((g) => [g.goal_id, g.name]))} />
              </SelectTrigger>
              <SelectContent>
                {goalList.map((goal) => (
                  <SelectItem key={goal.goal_id} value={goal.goal_id}>
                    {goal.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Date
            <Input type="date" className="w-36" value={date} onChange={(event) => setDate(event.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Amount (negative = withdrawal)
            <Input
              type="number"
              className="w-32"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              onBlur={handleCheck}
            />
          </label>
          <label className="flex flex-1 flex-col gap-1 text-xs text-muted-foreground">
            Note
            <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Optional" />
          </label>
          <Button size="sm" disabled={!goalId || parsedAmount === 0 || setContributions.isPending} onClick={handleSubmit}>
            Add
          </Button>
        </div>
        {result && (
          <div className="space-y-1 text-xs">
            {result.exceeds_unallocated && (
              <p className="flex items-center gap-1 text-destructive">
                <AlertTriangle className="size-3.5" />
                This exceeds unallocated money as of {date} ({formatCurrency(result.unallocated_as_of_date, 'USD')} available).
              </p>
            )}
            {!result.exceeds_unallocated && result.would_go_negative && (
              <p className="flex items-center gap-1 text-amber-600">
                <AlertTriangle className="size-3.5" />
                With this contribution, the next recurring-addition run is projected to leave unallocated at{' '}
                {formatCurrency(result.projected_next_run_unallocated, 'USD')}.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
