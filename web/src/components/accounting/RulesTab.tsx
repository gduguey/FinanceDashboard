import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useSetRules } from '@/hooks/useAccountingData'
import type { Account, Rule } from '@/types/accounting'

// The two placeholder counterparties every posting starts pointed at (see
// `accounting.store.UNCATEGORIZED_EXPENSE_ACCOUNT_ID`/`UNCATEGORIZED_INCOME_ACCOUNT_ID`)
// aren't things a rule ever repoints a posting *to* — a rule's whole job is
// to repoint a posting away from one of these, so they're excluded from the
// counterparty picker.
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])
const NO_COUNTERPARTY = '__none__'

function counterpartyOptions(accounts: Record<string, Account>): Account[] {
  return Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    .sort((a, b) => a.name.localeCompare(b.name))
}

function CounterpartySelect({
  accounts,
  value,
  onChange,
  disabled,
}: {
  accounts: Account[]
  value: string | null
  onChange: (accountId: string | null) => void
  disabled?: boolean
}) {
  const items = {
    [NO_COUNTERPARTY]: 'None',
    ...Object.fromEntries(accounts.map((account) => [account.account_id, account.name])),
  }
  return (
    <Select
      value={value ?? NO_COUNTERPARTY}
      onValueChange={(next) => onChange(next === NO_COUNTERPARTY ? null : (next ?? null))}
      disabled={disabled}
    >
      <SelectTrigger size="sm" className="w-48">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_COUNTERPARTY}>None</SelectItem>
        {accounts.map((account) => (
          <SelectItem key={account.account_id} value={account.account_id}>
            {account.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function RuleEditDialog({
  rule,
  accounts,
  onClose,
  onSave,
}: {
  rule: Rule
  accounts: Account[]
  onClose: () => void
  onSave: (rule: Rule) => void
}) {
  const [draft, setDraft] = useState(rule)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit rule</DialogTitle>
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
              value={draft.counterparty_account_id}
              onChange={(accountId) => setDraft((prev) => ({ ...prev, counterparty_account_id: accountId }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Priority (lower wins ties)
            <Input
              type="number"
              value={draft.priority}
              onChange={(event) => setDraft((prev) => ({ ...prev, priority: Number(event.target.value) }))}
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

export function RulesTab({ rules, accounts }: { rules: Rule[]; accounts: Record<string, Account> }) {
  const setRules = useSetRules()
  const [editing, setEditing] = useState<Rule | null>(null)
  const [draft, setDraft] = useState<{ descriptionContains: string; counterpartyAccountId: string | null }>({
    descriptionContains: '',
    counterpartyAccountId: null,
  })
  const { sorted, sort, toggleSort } = useSortableRows(rules, 'priority')
  const options = counterpartyOptions(accounts)
  const counterpartyName = (rule: Rule) =>
    (rule.counterparty_account_id && accounts[rule.counterparty_account_id]?.name) || '—'

  function addRule() {
    if (!draft.descriptionContains || !draft.counterpartyAccountId) return
    const rule: Rule = {
      rule_id: `manual:${Date.now()}`,
      description_contains: draft.descriptionContains,
      account_id: null,
      category_id: null,
      subcategory_id: null,
      counterparty_account_id: draft.counterpartyAccountId,
      priority: 100,
      description: '',
    }
    setRules.mutate([...rules, rule])
    setDraft({ descriptionContains: '', counterpartyAccountId: null })
  }

  function removeRule(ruleId: string) {
    setRules.mutate(rules.filter((rule) => rule.rule_id !== ruleId))
  }

  function saveRule(updated: Rule) {
    setRules.mutate(rules.map((rule) => (rule.rule_id === updated.rule_id ? updated : rule)))
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Rules</CardTitle>
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
              <SortableTableHead active={sort.key === 'priority'} desc={sort.desc} onClick={() => toggleSort('priority')}>
                Priority
              </SortableTableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((rule) => (
              <TableRow key={rule.rule_id}>
                <TableCell className="font-medium">{rule.description_contains}</TableCell>
                <TableCell className="text-muted-foreground">{counterpartyName(rule)}</TableCell>
                <TableCell className="max-w-xs truncate text-muted-foreground">{rule.description || '—'}</TableCell>
                <TableCell className="text-muted-foreground">{rule.priority}</TableCell>
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
            Add rule
          </Button>
        </div>
      </CardContent>
      {editing && (
        <RuleEditDialog rule={editing} accounts={options} onClose={() => setEditing(null)} onSave={saveRule} />
      )}
    </Card>
  )
}
