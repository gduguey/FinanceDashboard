import { GripVertical, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  useCreateContributionAutomation,
  useCreateWithdrawalAutomation,
  useDeleteGoalAutomation,
  usePatchGoalAutomation,
  useReorderContributionAutomations,
  useReorderWithdrawalAutomations,
} from '@/hooks/useAccountingData'
import { moveItem, sameMembers, sameOrder } from '@/lib/reorder'
import type { Goal, GoalAutomation, GoalAutomationFrequency, GoalAutomationMode } from '@/types/accounting'

const MODE_LABELS: Record<GoalAutomationMode, string> = {
  fixed_amount: 'Fixed amount',
  percent_of_unallocated: '% of unallocated',
  remainder: 'Remainder (whatever is left)',
}

const FREQUENCY_LABELS: Record<GoalAutomationFrequency, string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  biweekly: 'Biweekly',
  monthly: 'Monthly',
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

const keyOfAutomation = (automation: GoalAutomation) => automation.automation_id

// Native HTML5 drag-and-drop for row reordering — no extra dependency
// needed for a plain vertical-list reorder. `priority` is never sent from
// here at all: the reorder endpoints read it off the position of each id in
// the submitted list, so "drag to reorder" and "priority" have no way to
// drift apart from each other.
//
// The drag is painted from local state and sent once, on drop. It used to call
// the reorder mutation from `onDragOver`, i.e. on every hover event, so
// dragging a row down a list of ten sent up to nine requests whose responses
// could resolve out of order and leave an intermediate order persisted
// (docs/known-gaps.md gap 5).
function useRowDrag<T>(items: T[], keyOf: (item: T) => string, onReorder: (items: T[]) => void) {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null)
  // The order being dragged, held locally so hovering repaints without asking
  // the server. `null` means "nothing in flight, render what the server says".
  const [pending, setPending] = useState<T[] | null>(null)
  const rows = pending ?? items

  // Drop the local copy once the server's own order agrees with it. Clearing
  // it at drop time instead would snap the rows back to the pre-drag order for
  // as long as the request took, then forward again when it landed.
  //
  // Membership is the second reason to drop it, and the one agreement alone
  // cannot reach: an automation created or deleted while a reorder is
  // unresolved gives the server a list the pending copy can never equal, so
  // waiting on order alone would keep rendering the stale rows — a deleted one
  // included — for as long as the panel stayed mounted.
  useEffect(() => {
    if (pending === null) return
    if (sameOrder(pending, items, keyOf) || !sameMembers(pending, items, keyOf)) setPending(null)
  }, [pending, items, keyOf])

  return {
    rows,
    draggedIndex,
    onDragStart: (index: number) => () => {
      setDraggedIndex(index)
      setPending(rows)
    },
    onDragOver: (index: number) => (event: React.DragEvent) => {
      event.preventDefault()
      if (draggedIndex === null || draggedIndex === index) return
      // Local only. This fires on every hover event — dragging a row down a
      // list of ten produced up to nine requests, whose responses could resolve
      // out of order and leave an intermediate order persisted.
      setPending(moveItem(rows, draggedIndex, index))
      setDraggedIndex(index)
    },
    onDragEnd: () => {
      if (pending !== null && !sameOrder(pending, items, keyOf)) onReorder(pending)
      setDraggedIndex(null)
    },
  }
}

function goalName(goals: Record<string, Goal>, goalId: string): string {
  return goals[goalId]?.name ?? goalId
}

function RecurringAdditionsList({ additions, goals }: { additions: GoalAutomation[]; goals: Record<string, Goal> }) {
  const reorderAdditions = useReorderContributionAutomations()
  const patchAddition = usePatchGoalAutomation()
  const deleteAddition = useDeleteGoalAutomation()
  const createAddition = useCreateContributionAutomation()
  const ordered = [...additions].sort((a, b) => a.priority - b.priority)
  const goalList = Object.values(goals)
  // Drag-to-reorder is the one operation spanning the whole list — it renumbers
  // every rule's priority at once, which is why it is a single request and not
  // one PATCH per moved row.
  const drag = useRowDrag(ordered, keyOfAutomation, (next) =>
    reorderAdditions.mutate(next.map((addition) => addition.automation_id)),
  )

  // A single-rule field edit is scoped to its own id (last-write-wins), so it can't revert a
  // concurrent edit to a different rule the way the old whole-list PUT could.
  // Every row here is a `contribution` automation, so its schedule fields are
  // never actually null (the API's own CHECK guarantees it) — the fallbacks
  // below only satisfy the shared `GoalAutomation` type, which has to allow
  // null for the withdrawal rows sharing it.
  function update(automationId: string, patch: Partial<GoalAutomation>) {
    const existing = ordered.find((a) => a.automation_id === automationId)
    if (!existing) return
    const merged = { ...existing, ...patch }
    patchAddition.mutate({
      automationId,
      update: {
        goal_id: merged.goal_id,
        start_date: merged.start_date ?? today(),
        frequency: merged.frequency ?? 'monthly',
        end_date: merged.end_date,
        mode: merged.mode ?? 'fixed_amount',
        value: merged.value ?? 0,
        currency: merged.currency ?? 'USD',
        priority: merged.priority,
      },
    })
  }

  function remove(automationId: string) {
    deleteAddition.mutate(automationId)
  }

  function add() {
    if (goalList.length === 0) return
    createAddition.mutate({
      goal_id: goalList[0].goal_id,
      start_date: today(),
      frequency: 'monthly',
      end_date: null,
      mode: 'fixed_amount',
      value: 0,
      currency: 'USD',
    })
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
      <CardContent>
        {/* An ordered list, because the order is the meaning — the card's own
            description says these run top to bottom. `<li>` also gives the
            drag handlers a semantic element to sit on. */}
        <ol className="space-y-2">
          {drag.rows.map((addition, index) => {
            const isLast = index === ordered.length - 1
            return (
              <li
                key={addition.automation_id}
                draggable
                onDragStart={drag.onDragStart(index)}
                onDragOver={drag.onDragOver(index)}
                onDragEnd={drag.onDragEnd}
                className="flex flex-wrap items-center gap-2 rounded-md border p-2"
              >
                <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" />
                <Select
                  value={addition.goal_id}
                  onValueChange={(value) => value && update(addition.automation_id, { goal_id: value })}
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
                <Field label="Starting" className="flex-row items-center gap-1">
                  {(id) => (
                    <Input
                      id={id}
                      type="date"
                      className="w-36"
                      value={addition.start_date ?? ''}
                      onChange={(event) => update(addition.automation_id, { start_date: event.target.value })}
                    />
                  )}
                </Field>
                <Select
                  value={addition.frequency ?? 'monthly'}
                  onValueChange={(value) =>
                    value && update(addition.automation_id, { frequency: value as GoalAutomationFrequency })
                  }
                >
                  <SelectTrigger size="sm" className="min-w-28">
                    <SelectValue items={FREQUENCY_LABELS} />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.entries(FREQUENCY_LABELS) as [GoalAutomationFrequency, string][]).map(
                      ([frequency, label]) => (
                        <SelectItem key={frequency} value={frequency}>
                          {label}
                        </SelectItem>
                      ),
                    )}
                  </SelectContent>
                </Select>
                <Field label="Until (optional)" className="flex-row items-center gap-1">
                  {(id) => (
                    <OptionalDateInput
                      id={id}
                      value={addition.end_date ?? ''}
                      onChange={(value) => update(addition.automation_id, { end_date: value || null })}
                      placeholder="No end date"
                    />
                  )}
                </Field>
                <Select
                  value={addition.mode ?? 'fixed_amount'}
                  onValueChange={(value) => update(addition.automation_id, { mode: value as GoalAutomationMode })}
                >
                  <SelectTrigger size="sm" className="min-w-44">
                    <SelectValue items={MODE_LABELS} />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.entries(MODE_LABELS) as [GoalAutomationMode, string][]).map(([mode, label]) => (
                      <SelectItem key={mode} value={mode} disabled={mode === 'remainder' && !isLast}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {addition.mode !== 'remainder' && (
                  <NumberInput
                    className="w-24"
                    value={addition.value ?? 0}
                    onCommit={(value) => update(addition.automation_id, { value: value ?? 0 })}
                  />
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete the automation for ${goalName(goals, addition.goal_id)}`}
                  onClick={() => remove(addition.automation_id)}
                >
                  <Trash2 className="size-3.5 text-muted-foreground" />
                </Button>
              </li>
            )
          })}
        </ol>
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={add}
          disabled={goalList.length === 0 || createAddition.isPending}
        >
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
  priorities: GoalAutomation[]
  goals: Record<string, Goal>
}) {
  const reorderPriorities = useReorderWithdrawalAutomations()
  const createPriority = useCreateWithdrawalAutomation()
  const deletePriority = useDeleteGoalAutomation()
  const ordered = [...priorities].sort((a, b) => a.priority - b.priority)
  const goalList = Object.values(goals)
  const drag = useRowDrag(ordered, keyOfAutomation, (next) =>
    reorderPriorities.mutate(next.map((entry) => entry.automation_id)),
  )
  const unranked = goalList.filter((goal) => !ordered.some((entry) => entry.goal_id === goal.goal_id))

  // Joining and leaving the drawdown order are their own requests — the reorder
  // route only permutes the entries already in it. Neither call needs to mint an
  // id: the create derives one from the goal server-side, and every row rendered
  // here already carries the `automation_id` the delete addresses.
  function remove(automationId: string) {
    deletePriority.mutate(automationId)
  }

  function add(goalId: string) {
    createPriority.mutate(goalId)
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
      <CardContent>
        {/* Ordered for the same reason as the contributions list above. */}
        <ol className="space-y-2">
          {drag.rows.map((entry, index) => (
            <li
              key={entry.goal_id}
              draggable
              onDragStart={drag.onDragStart(index)}
              onDragOver={drag.onDragOver(index)}
              onDragEnd={drag.onDragEnd}
              className="flex items-center gap-2 rounded-md border p-2"
            >
              <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" />
              <span className="flex-1 text-sm">{goalName(goals, entry.goal_id)}</span>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${goalName(goals, entry.goal_id)} from the drawdown order`}
                onClick={() => remove(entry.automation_id)}
              >
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            </li>
          ))}
        </ol>
        {unranked.length > 0 && (
          <Select onValueChange={(value) => value && add(String(value))}>
            <SelectTrigger size="sm" className="mt-2 min-w-48">
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
  automations,
}: {
  goals: Record<string, Goal>
  automations: GoalAutomation[]
}) {
  // One list arrives from the store (one `goal_automations` table); `direction`
  // is what splits it back into the two panels the page shows.
  return (
    <div className="space-y-4">
      <RecurringAdditionsList
        additions={automations.filter((automation) => automation.direction === 'contribution')}
        goals={goals}
      />
      <WithdrawalPrioritiesList
        priorities={automations.filter((automation) => automation.direction === 'withdrawal')}
        goals={goals}
      />
    </div>
  )
}
