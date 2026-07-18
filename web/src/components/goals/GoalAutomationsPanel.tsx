import { GripVertical, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useSetRecurringAdditions, useSetWithdrawalPriorities } from '@/hooks/useAccountingData'
import type {
  Goal,
  RecurringAddition,
  RecurringAdditionFrequency,
  RecurringAdditionMode,
  WithdrawalPriorityEntry,
} from '@/types/accounting'

const MODE_LABELS: Record<RecurringAdditionMode, string> = {
  fixed_amount: 'Fixed amount',
  percent_of_unallocated: '% of unallocated',
  remainder: 'Remainder (whatever is left)',
}

const FREQUENCY_LABELS: Record<RecurringAdditionFrequency, string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  biweekly: 'Biweekly',
  monthly: 'Monthly',
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

// Native HTML5 drag-and-drop for row reordering — no extra dependency
// needed for a plain vertical-list reorder. `priority` is never edited
// directly; it's always recomputed as the list's own array order right
// before persisting, so "drag to reorder" and "priority" can never drift
// apart from each other.
function useRowDrag<T>(items: T[], onReorder: (items: T[]) => void) {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null)
  return {
    draggedIndex,
    onDragStart: (index: number) => () => setDraggedIndex(index),
    onDragOver: (index: number) => (event: React.DragEvent) => {
      event.preventDefault()
      if (draggedIndex === null || draggedIndex === index) return
      const next = [...items]
      const [moved] = next.splice(draggedIndex, 1)
      next.splice(index, 0, moved)
      setDraggedIndex(index)
      onReorder(next)
    },
    onDragEnd: () => setDraggedIndex(null),
  }
}

function goalName(goals: Record<string, Goal>, goalId: string): string {
  return goals[goalId]?.name ?? goalId
}

function RecurringAdditionsList({ additions, goals }: { additions: RecurringAddition[]; goals: Record<string, Goal> }) {
  const setAdditions = useSetRecurringAdditions()
  const ordered = [...additions].sort((a, b) => a.priority - b.priority)
  const goalList = Object.values(goals)
  const drag = useRowDrag(ordered, (next) => persist(next))

  function persist(next: RecurringAddition[]) {
    setAdditions.mutate(next.map((addition, index) => ({ ...addition, priority: index })))
  }

  function update(additionId: string, patch: Partial<RecurringAddition>) {
    persist(ordered.map((a) => (a.addition_id === additionId ? { ...a, ...patch } : a)))
  }

  function remove(additionId: string) {
    persist(ordered.filter((a) => a.addition_id !== additionId))
  }

  function add() {
    if (goalList.length === 0) return
    persist([
      ...ordered,
      {
        addition_id: `addition:${Date.now()}`,
        goal_id: goalList[0].goal_id,
        start_date: today(),
        frequency: 'monthly',
        end_date: null,
        mode: 'fixed_amount',
        value: 0,
        currency: 'USD',
        priority: ordered.length,
      },
    ])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recurring additions</CardTitle>
        <CardDescription>
          Run in this order whenever each one's schedule is due — a fixed-amount row funded first can leave less for a
          lower one. Drag to reorder; only the bottom row may be "Remainder".
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {ordered.map((addition, index) => {
          const isLast = index === ordered.length - 1
          return (
            <div
              key={addition.addition_id}
              draggable
              onDragStart={drag.onDragStart(index)}
              onDragOver={drag.onDragOver(index)}
              onDragEnd={drag.onDragEnd}
              className="flex flex-wrap items-center gap-2 rounded-md border p-2"
            >
              <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" />
              <Select
                value={addition.goal_id}
                onValueChange={(value) => value && update(addition.addition_id, { goal_id: value })}
              >
                <SelectTrigger size="sm" className="min-w-36">
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
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                Starting
                <Input
                  type="date"
                  className="w-36"
                  value={addition.start_date}
                  onChange={(event) => update(addition.addition_id, { start_date: event.target.value })}
                />
              </label>
              <Select
                value={addition.frequency}
                onValueChange={(value) =>
                  value && update(addition.addition_id, { frequency: value as RecurringAdditionFrequency })
                }
              >
                <SelectTrigger size="sm" className="min-w-28">
                  <SelectValue items={FREQUENCY_LABELS} />
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(FREQUENCY_LABELS) as [RecurringAdditionFrequency, string][]).map(
                    ([frequency, label]) => (
                      <SelectItem key={frequency} value={frequency}>
                        {label}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                Until (optional)
                <OptionalDateInput
                  value={addition.end_date ?? ''}
                  onChange={(value) => update(addition.addition_id, { end_date: value || null })}
                  placeholder="No end date"
                />
              </label>
              <Select
                value={addition.mode}
                onValueChange={(value) => update(addition.addition_id, { mode: value as RecurringAdditionMode })}
              >
                <SelectTrigger size="sm" className="min-w-44">
                  <SelectValue items={MODE_LABELS} />
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(MODE_LABELS) as [RecurringAdditionMode, string][]).map(([mode, label]) => (
                    <SelectItem key={mode} value={mode} disabled={mode === 'remainder' && !isLast}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {addition.mode !== 'remainder' && (
                <NumberInput
                  className="w-24"
                  value={addition.value}
                  onCommit={(value) => update(addition.addition_id, { value: value ?? 0 })}
                />
              )}
              <Button variant="ghost" size="icon" onClick={() => remove(addition.addition_id)}>
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            </div>
          )
        })}
        <Button variant="outline" size="sm" onClick={add} disabled={goalList.length === 0}>
          + Add recurring addition
        </Button>
      </CardContent>
    </Card>
  )
}

function WithdrawalPrioritiesList({
  priorities,
  goals,
}: {
  priorities: WithdrawalPriorityEntry[]
  goals: Record<string, Goal>
}) {
  const setPriorities = useSetWithdrawalPriorities()
  const ordered = [...priorities].sort((a, b) => a.priority - b.priority)
  const goalList = Object.values(goals)
  const drag = useRowDrag(ordered, (next) => persist(next))
  const unranked = goalList.filter((goal) => !ordered.some((entry) => entry.goal_id === goal.goal_id))

  function persist(next: WithdrawalPriorityEntry[]) {
    setPriorities.mutate(next.map((entry, index) => ({ ...entry, priority: index })))
  }

  function remove(goalId: string) {
    persist(ordered.filter((entry) => entry.goal_id !== goalId))
  }

  function add(goalId: string) {
    persist([...ordered, { goal_id: goalId, priority: ordered.length }])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Withdrawal priority</CardTitle>
        <CardDescription>
          If unallocated money ever goes negative, goals are drawn down in this order until it's back to zero (or every
          listed goal is exhausted). Drag to reorder.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {ordered.map((entry, index) => (
          <div
            key={entry.goal_id}
            draggable
            onDragStart={drag.onDragStart(index)}
            onDragOver={drag.onDragOver(index)}
            onDragEnd={drag.onDragEnd}
            className="flex items-center gap-2 rounded-md border p-2"
          >
            <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" />
            <span className="flex-1 text-sm">{goalName(goals, entry.goal_id)}</span>
            <Button variant="ghost" size="icon" onClick={() => remove(entry.goal_id)}>
              <Trash2 className="size-3.5 text-muted-foreground" />
            </Button>
          </div>
        ))}
        {unranked.length > 0 && (
          <Select onValueChange={(value) => value && add(String(value))}>
            <SelectTrigger size="sm" className="min-w-48">
              <SelectValue items={{ '': '+ Add a goal to the priority list' }} />
            </SelectTrigger>
            <SelectContent>
              {unranked.map((goal) => (
                <SelectItem key={goal.goal_id} value={goal.goal_id}>
                  {goal.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </CardContent>
    </Card>
  )
}

export function GoalAutomationsPanel({
  goals,
  recurringAdditions,
  withdrawalPriorities,
}: {
  goals: Record<string, Goal>
  recurringAdditions: RecurringAddition[]
  withdrawalPriorities: WithdrawalPriorityEntry[]
}) {
  return (
    <div className="space-y-4">
      <RecurringAdditionsList additions={recurringAdditions} goals={goals} />
      <WithdrawalPrioritiesList priorities={withdrawalPriorities} goals={goals} />
    </div>
  )
}
