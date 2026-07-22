import { AlertTriangle, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { FilterSelect } from '@/components/shared/FilterSelect'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  useCreateGoalContribution,
  useCurrencies,
  useRatesToBase,
  useRemoveGoalContribution,
  useSimulateContribution,
  useUpdateGoalContribution,
} from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { BASE_CURRENCY, convertCurrency } from '@/lib/currency'
import { FILTER_ALL as ALL, matchesFilter } from '@/lib/filters'
import { formatCurrency } from '@/lib/format'
import type { Goal, GoalContribution } from '@/types/accounting'

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10)
}

// Locally-controlled so typing a note doesn't fire one PATCH per keystroke — it commits once, on blur
// (matching how the Amount field uses `onCommit`). Without this, several in-flight per-keystroke writes
// used to race each other and could surface a spurious version-conflict toast to a user just typing.
function NoteCell({ value, onCommit }: { value: string; onCommit: (note: string) => void }) {
  const [draft, setDraft] = useState(value)
  // Re-sync when the persisted value changes out from under us (e.g. a refetch), but never mid-typing.
  const [lastSynced, setLastSynced] = useState(value)
  if (value !== lastSynced) {
    setLastSynced(value)
    setDraft(value)
  }
  return (
    <Input
      className="h-7 text-xs"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
    />
  )
}

// Reuses the Transactions table's own patterns (per-column is/is-not
// filters, click-to-sort headers) — not virtualized, unlike Transactions,
// since a goal's contribution history realistically stays in the dozens
// to low hundreds of rows, nowhere near the thousands that motivated
// virtualizing postings.
export function ContributionLedgerTable({
  contributions,
  goals,
}: {
  contributions: Record<string, GoalContribution>
  goals: Record<string, Goal>
}) {
  const createContribution = useCreateGoalContribution()
  const updateContribution = useUpdateGoalContribution()
  const removeContribution = useRemoveGoalContribution()
  const simulate = useSimulateContribution()
  const { data: currencies } = useCurrencies()
  const ratesToBase = useRatesToBase(
    (currencies ?? []).map((currency) => currency.code).filter((code) => code !== BASE_CURRENCY),
  )
  const [goalFilter, setGoalFilter] = useState(ALL)
  const [originFilter, setOriginFilter] = useState(ALL)
  const [goalExclude, setGoalExclude] = useState(false)
  const [originExclude, setOriginExclude] = useState(false)
  // Keyed by contribution_id — populated on blur of that row's date/amount
  // cell, the same "check before it's too late" behavior the retired
  // "Add a contribution" form used to give on its own submit-time check.
  const [warnings, setWarnings] = useState<Record<string, string>>({})

  const goalList = Object.values(goals).sort((a, b) => a.name.localeCompare(b.name))
  const rows = Object.values(contributions)
  const filtered = useMemo(
    () =>
      rows
        .filter((row) => matchesFilter(row.goal_id === goalFilter, goalFilter, goalExclude))
        .filter((row) => matchesFilter(row.origin === originFilter, originFilter, originExclude)),
    [rows, goalFilter, goalExclude, originFilter, originExclude],
  )
  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'date')

  function update(contributionId: string, patch: Partial<GoalContribution>) {
    const existing = contributions[contributionId]
    if (!existing) return
    const wasAutomated = existing.origin === 'automation'
    const { contribution_id: _id, ...merged } = { ...existing, ...patch, edited: wasAutomated ? true : existing.edited }
    updateContribution.mutate({ contributionId, contribution: merged })
  }

  function remove(contributionId: string) {
    removeContribution.mutate(contributionId)
    setWarnings(({ [contributionId]: _removedWarning, ...restWarnings }) => restWarnings)
  }

  function addRow() {
    if (goalList.length === 0) return
    createContribution.mutate({
      goal_id: goalList[0].goal_id,
      date: new Date(todayIsoDate()).toISOString(),
      amount: 0,
      // A contribution is always denominated in its own goal's currency
      // (see `changeGoal`) — never a separately-chosen currency — so
      // there's never a mismatch between "what this row says" and "what
      // the goal it funds is tracked in".
      currency: goalList[0].target_currency,
      note: '',
      source_posting_id: null,
      origin: 'manual',
      edited: false,
    })
  }

  // Re-pointing a contribution at a different goal also re-denominates it
  // into that goal's own currency, converting the stored amount so the
  // real value moved is preserved — not just relabeling the same number
  // into a different unit. This is the one place a contribution's
  // currency ever changes, keeping "contribution.currency === its goal's
  // target_currency" true at all times.
  function changeGoal(contribution: GoalContribution, goalId: string) {
    const nextGoal = goals[goalId]
    if (!nextGoal) return
    const amount = convertCurrency(contribution.amount, contribution.currency, nextGoal.target_currency, ratesToBase)
    update(contribution.contribution_id, { goal_id: goalId, currency: nextGoal.target_currency, amount })
  }

  async function checkContribution(contribution: GoalContribution) {
    const result = await simulate.mutateAsync({
      goalId: contribution.goal_id,
      date: contribution.date.slice(0, 10),
      amount: contribution.amount,
    })
    let message: string | null = null
    if (result.exceeds_unallocated) {
      message = `Exceeds unallocated as of ${contribution.date.slice(0, 10)} (${formatCurrency(result.unallocated_as_of_date, 'USD')} available)`
    } else if (result.would_go_negative) {
      message = `Next recurring-addition run is projected to leave unallocated at ${formatCurrency(result.projected_next_run_unallocated, 'USD')}`
    }
    setWarnings((prev) => {
      if (message === null) {
        const { [contribution.contribution_id]: _removed, ...rest } = prev
        return rest
      }
      return { ...prev, [contribution.contribution_id]: message }
    })
  }

  const goalItems = { [ALL]: 'All goals', ...Object.fromEntries(Object.values(goals).map((g) => [g.goal_id, g.name])) }
  const originItems = { [ALL]: 'All', manual: 'Manual', automation: 'Automation' }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <CardTitle>Contribution ledger</CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={addRow}
            disabled={goalList.length === 0 || createContribution.isPending}
            title="Add a contribution"
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterSelect
            value={goalFilter}
            exclude={goalExclude}
            items={goalItems}
            width="min-w-36"
            onValueChange={setGoalFilter}
            onExcludeChange={setGoalExclude}
          />
          <FilterSelect
            value={originFilter}
            exclude={originExclude}
            items={originItems}
            width="min-w-28"
            onValueChange={setOriginFilter}
            onExcludeChange={setOriginExclude}
          />
        </div>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No contributions match.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'date'} desc={sort.desc} onClick={() => toggleSort('date')}>
                  Date
                </SortableTableHead>
                <SortableTableHead
                  active={sort.key === 'goal_id'}
                  desc={sort.desc}
                  onClick={() => toggleSort('goal_id')}
                >
                  Goal
                </SortableTableHead>
                <SortableTableHead
                  align="right"
                  active={sort.key === 'amount'}
                  desc={sort.desc}
                  onClick={() => toggleSort('amount')}
                >
                  Amount
                </SortableTableHead>
                <TableHead>Note</TableHead>
                <SortableTableHead active={sort.key === 'origin'} desc={sort.desc} onClick={() => toggleSort('origin')}>
                  Origin
                </SortableTableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((contribution) => (
                <TableRow key={contribution.contribution_id}>
                  <TableCell className="whitespace-nowrap">
                    <Input
                      type="date"
                      className="h-7 w-32 text-xs"
                      value={contribution.date.slice(0, 10)}
                      onChange={(event) =>
                        update(contribution.contribution_id, { date: new Date(event.target.value).toISOString() })
                      }
                      onBlur={() => checkContribution(contribution)}
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <Select
                      value={contribution.goal_id}
                      onValueChange={(value) => value && changeGoal(contribution, value)}
                    >
                      <SelectTrigger size="sm" className="h-7 min-w-32 text-xs">
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
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <div className="flex items-center justify-end gap-1">
                      {warnings[contribution.contribution_id] && (
                        <Tooltip>
                          <TooltipTrigger>
                            <AlertTriangle className="size-3.5 text-amber-500" />
                          </TooltipTrigger>
                          <TooltipContent>{warnings[contribution.contribution_id]}</TooltipContent>
                        </Tooltip>
                      )}
                      <NumberInput
                        className="h-7 w-24 text-right text-xs"
                        value={contribution.amount}
                        onCommit={(amount) => {
                          const resolved = amount ?? 0
                          update(contribution.contribution_id, { amount: resolved })
                          checkContribution({ ...contribution, amount: resolved })
                        }}
                      />
                      <span className="w-9 text-left text-xs text-muted-foreground">{contribution.currency}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <NoteCell
                      value={contribution.note}
                      onCommit={(note) => update(contribution.contribution_id, { note })}
                    />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {contribution.origin === 'automation'
                      ? contribution.edited
                        ? 'Automation (edited)'
                        : 'Automation'
                      : 'Manual'}
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="icon" onClick={() => remove(contribution.contribution_id)}>
                      <Trash2 className="size-3.5 text-muted-foreground" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
