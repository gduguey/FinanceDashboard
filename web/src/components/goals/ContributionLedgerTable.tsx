import { AlertTriangle, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  useCurrencies,
  useRatesToBase,
  useSetGoalContributions,
  useSimulateContribution,
} from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency } from '@/lib/format'
import type { Goal, GoalContribution } from '@/types/accounting'

const ALL = '__all__'

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10)
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
  const setContributions = useSetGoalContributions()
  const simulate = useSimulateContribution()
  const { data: currencies } = useCurrencies()
  const ratesToBase = useRatesToBase(
    (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD'),
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
        .filter((row) => goalFilter === ALL || goalExclude !== (row.goal_id === goalFilter))
        .filter((row) => originFilter === ALL || originExclude !== (row.origin === originFilter)),
    [rows, goalFilter, goalExclude, originFilter, originExclude],
  )
  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'date')

  function update(contributionId: string, patch: Partial<GoalContribution>) {
    const existing = contributions[contributionId]
    if (!existing) return
    const wasAutomated = existing.origin === 'automation'
    setContributions.mutate({
      ...contributions,
      [contributionId]: { ...existing, ...patch, edited: wasAutomated ? true : existing.edited },
    })
  }

  function remove(contributionId: string) {
    const { [contributionId]: _removed, ...rest } = contributions
    setContributions.mutate(rest)
    setWarnings(({ [contributionId]: _removedWarning, ...restWarnings }) => restWarnings)
  }

  function addRow() {
    if (goalList.length === 0) return
    const contributionId = `manual:${Date.now()}`
    setContributions.mutate({
      ...contributions,
      [contributionId]: {
        contribution_id: contributionId,
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
      },
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
            disabled={goalList.length === 0}
            title="Add a contribution"
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            <Select value={goalFilter} onValueChange={(value) => value && setGoalFilter(value)}>
              <SelectTrigger size="sm" className="min-w-36">
                <SelectValue items={goalItems} />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(goalItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {goalFilter !== ALL && (
              <Button
                variant={goalExclude ? 'default' : 'outline'}
                size="sm"
                className="h-8 px-2 text-xs"
                onClick={() => setGoalExclude((v) => !v)}
              >
                {goalExclude ? 'Not' : 'Is'}
              </Button>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Select value={originFilter} onValueChange={(value) => value && setOriginFilter(value)}>
              <SelectTrigger size="sm" className="min-w-28">
                <SelectValue items={originItems} />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(originItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {originFilter !== ALL && (
              <Button
                variant={originExclude ? 'default' : 'outline'}
                size="sm"
                className="h-8 px-2 text-xs"
                onClick={() => setOriginExclude((v) => !v)}
              >
                {originExclude ? 'Not' : 'Is'}
              </Button>
            )}
          </div>
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
                <TableHead>Source</TableHead>
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
                      <Input
                        type="number"
                        className="h-7 w-24 text-right text-xs"
                        value={contribution.amount}
                        onChange={(event) =>
                          update(contribution.contribution_id, { amount: Number(event.target.value) })
                        }
                        onBlur={() => checkContribution(contribution)}
                      />
                      <span className="w-9 text-left text-xs text-muted-foreground">{contribution.currency}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Input
                      className="h-7 text-xs"
                      value={contribution.note}
                      onChange={(event) => update(contribution.contribution_id, { note: event.target.value })}
                    />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {contribution.source_posting_id ?? '—'}
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
