import { useMemo, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useSetGoalContributions } from '@/hooks/useAccountingData'
import type { Goal, GoalContribution } from '@/types/accounting'

const ALL = '__all__'

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
  const [goalFilter, setGoalFilter] = useState(ALL)
  const [originFilter, setOriginFilter] = useState(ALL)
  const [goalExclude, setGoalExclude] = useState(false)
  const [originExclude, setOriginExclude] = useState(false)

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
  }

  const goalItems = { [ALL]: 'All goals', ...Object.fromEntries(Object.values(goals).map((g) => [g.goal_id, g.name])) }
  const originItems = { [ALL]: 'All', manual: 'Manual', automation: 'Automation' }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Contribution ledger</CardTitle>
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
              <Button variant={goalExclude ? 'default' : 'outline'} size="sm" className="h-8 px-2 text-xs" onClick={() => setGoalExclude((v) => !v)}>
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
              <Button variant={originExclude ? 'default' : 'outline'} size="sm" className="h-8 px-2 text-xs" onClick={() => setOriginExclude((v) => !v)}>
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
                <SortableTableHead active={sort.key === 'goal_id'} desc={sort.desc} onClick={() => toggleSort('goal_id')}>
                  Goal
                </SortableTableHead>
                <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
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
                      onChange={(event) => update(contribution.contribution_id, { date: new Date(event.target.value).toISOString() })}
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{goals[contribution.goal_id]?.name ?? contribution.goal_id}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    <Input
                      type="number"
                      className="h-7 w-24 text-right text-xs"
                      value={contribution.amount}
                      onChange={(event) => update(contribution.contribution_id, { amount: Number(event.target.value) })}
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      className="h-7 text-xs"
                      value={contribution.note}
                      onChange={(event) => update(contribution.contribution_id, { note: event.target.value })}
                    />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{contribution.source_posting_id ?? '—'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {contribution.origin === 'automation' ? (contribution.edited ? 'Automation (edited)' : 'Automation') : 'Manual'}
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
