import { Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { CounterpartySelect } from '@/components/shared/CounterpartySelect'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { useSetTransferRules } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { counterpartyOptions } from '@/lib/counterpartyAccounts'
import type { Account, TransferRule } from '@/types/accounting'

function TransferRuleEditDialog({
  rule,
  accounts,
  onClose,
  onSave,
}: {
  rule: TransferRule
  accounts: Account[]
  onClose: () => void
  onSave: (rule: TransferRule) => void
}) {
  const [draft, setDraft] = useState(rule)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit transfer rule</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            If description contains
            <Input
              value={draft.description_contains}
              onChange={(event) => setDraft((prev) => ({ ...prev, description_contains: event.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Counterparty
            <CounterpartySelect
              accounts={accounts}
              value={draft.counterparty_account_id ?? null}
              onChange={(accountId) => setDraft((prev) => ({ ...prev, counterparty_account_id: accountId }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Priority (lower wins ties)
            <NumberInput
              value={draft.priority}
              onCommit={(priority) => setDraft((prev) => ({ ...prev, priority: priority ?? 0 }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            What does this rule actually do? (notes for future you)
            <Textarea
              value={draft.description}
              onChange={(event) => setDraft((prev) => ({ ...prev, description: event.target.value }))}
              placeholder="e.g. Catches my Chase credit card autopay so it doesn't show up as a real expense"
            />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onSave(draft)
              onClose()
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TransferRulesTab({ rules, accounts }: { rules: TransferRule[]; accounts: Record<string, Account> }) {
  const setRules = useSetTransferRules()
  const [editing, setEditing] = useState<TransferRule | null>(null)
  const [draft, setDraft] = useState<{ descriptionContains: string; counterpartyAccountId: string | null }>({
    descriptionContains: '',
    counterpartyAccountId: null,
  })
  const { sorted, sort, toggleSort } = useSortableRows(rules, 'priority')
  const options = counterpartyOptions(accounts)
  const counterpartyName = (rule: TransferRule) =>
    (rule.counterparty_account_id && accounts[rule.counterparty_account_id]?.name) || '—'

  function addRule() {
    if (!draft.descriptionContains || !draft.counterpartyAccountId) return
    const rule: TransferRule = {
      rule_id: `manual:${Date.now()}`,
      description_contains: draft.descriptionContains,
      account_id: null,
      counterparty_account_id: draft.counterpartyAccountId,
      priority: 100,
      description: '',
      active: true,
    }
    setRules.mutate([...rules, rule])
    setDraft({ descriptionContains: '', counterpartyAccountId: null })
  }

  function removeRule(ruleId: string) {
    setRules.mutate(rules.filter((rule) => rule.rule_id !== ruleId))
  }

  function saveRule(updated: TransferRule) {
    setRules.mutate(rules.map((rule) => (rule.rule_id === updated.rule_id ? updated : rule)))
  }

  function toggleActive(ruleId: string, active: boolean) {
    setRules.mutate(rules.map((rule) => (rule.rule_id === ruleId ? { ...rule, active } : rule)))
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Transfer rules</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'description_contains'}
                desc={sort.desc}
                onClick={() => toggleSort('description_contains')}
              >
                If description contains
              </SortableTableHead>
              <TableHead>Counterparty</TableHead>
              <TableHead>Notes</TableHead>
              <SortableTableHead
                active={sort.key === 'priority'}
                desc={sort.desc}
                onClick={() => toggleSort('priority')}
              >
                Priority
              </SortableTableHead>
              <TableHead>Active</TableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                  No rules yet — add one below to automatically categorize recurring transfers.
                </TableCell>
              </TableRow>
            )}
            {sorted.map((rule) => (
              <TableRow key={rule.rule_id} className={rule.active ? '' : 'opacity-50'}>
                <TableCell className="font-medium">{rule.description_contains}</TableCell>
                <TableCell className="text-muted-foreground">{counterpartyName(rule)}</TableCell>
                <TableCell className="max-w-xs text-muted-foreground">
                  {rule.description ? <Truncate text={rule.description} /> : '—'}
                </TableCell>
                <TableCell className="text-muted-foreground">{rule.priority}</TableCell>
                <TableCell>
                  <Switch
                    size="sm"
                    checked={rule.active}
                    onCheckedChange={(checked) => toggleActive(rule.rule_id, checked)}
                  />
                </TableCell>
                <TableCell className="flex gap-1">
                  <Button variant="ghost" size="icon" onClick={() => setEditing(rule)}>
                    <Pencil className="size-3.5 text-muted-foreground" />
                  </Button>
                  <Button variant="ghost" size="icon" onClick={() => removeRule(rule.rule_id)}>
                    <Trash2 className="size-3.5 text-muted-foreground" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Description contains
            <Input
              className="w-56"
              value={draft.descriptionContains}
              onChange={(event) => setDraft((prev) => ({ ...prev, descriptionContains: event.target.value }))}
              placeholder="e.g. NETFLIX"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Counterparty
            <CounterpartySelect
              accounts={options}
              value={draft.counterpartyAccountId}
              onChange={(accountId) => setDraft((prev) => ({ ...prev, counterpartyAccountId: accountId }))}
            />
          </label>
          <Button size="sm" onClick={addRule}>
            Add transfer rule
          </Button>
        </div>
      </CardContent>
      {editing && (
        <TransferRuleEditDialog rule={editing} accounts={options} onClose={() => setEditing(null)} onSave={saveRule} />
      )}
    </Card>
  )
}
