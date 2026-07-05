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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useSetRules } from '@/hooks/useAccountingData'
import type { Rule } from '@/types/accounting'

function RuleEditDialog({ rule, onClose, onSave }: { rule: Rule; onClose: () => void; onSave: (rule: Rule) => void }) {
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
            Counterparty name
            <Input
              value={draft.counterparty_account_name ?? ''}
              onChange={(event) => setDraft((prev) => ({ ...prev, counterparty_account_name: event.target.value }))}
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

export function RulesTab({ rules }: { rules: Rule[] }) {
  const setRules = useSetRules()
  const [editing, setEditing] = useState<Rule | null>(null)
  const [draft, setDraft] = useState({ descriptionContains: '', counterpartyAccountId: '', counterpartyAccountName: '' })
  const { sorted, sort, toggleSort } = useSortableRows(rules, 'priority')

  function addRule() {
    if (!draft.descriptionContains || !draft.counterpartyAccountId) return
    const rule: Rule = {
      rule_id: `manual:${Date.now()}`,
      description_contains: draft.descriptionContains,
      account_id: null,
      category_id: null,
      subcategory_id: null,
      counterparty_account_id: draft.counterpartyAccountId,
      counterparty_account_name: draft.counterpartyAccountName || draft.counterpartyAccountId,
      counterparty_account_kind: 'expense_payee',
      counterparty_parent_account_id: null,
      priority: 100,
      description: '',
    }
    setRules.mutate([...rules, rule])
    setDraft({ descriptionContains: '', counterpartyAccountId: '', counterpartyAccountName: '' })
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
              <SortableTableHead
                active={sort.key === 'counterparty_account_name'}
                desc={sort.desc}
                onClick={() => toggleSort('counterparty_account_name')}
              >
                Counterparty
              </SortableTableHead>
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
                <TableCell className="text-muted-foreground">{rule.counterparty_account_name}</TableCell>
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
            Counterparty account id
            <Input
              className="w-48"
              value={draft.counterpartyAccountId}
              onChange={(event) => setDraft((prev) => ({ ...prev, counterpartyAccountId: event.target.value }))}
              placeholder="e.g. payee:netflix"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Counterparty name
            <Input
              className="w-48"
              value={draft.counterpartyAccountName}
              onChange={(event) => setDraft((prev) => ({ ...prev, counterpartyAccountName: event.target.value }))}
              placeholder="e.g. Netflix"
            />
          </label>
          <Button size="sm" onClick={addRule}>
            Add rule
          </Button>
        </div>
      </CardContent>
      {editing && <RuleEditDialog rule={editing} onClose={() => setEditing(null)} onSave={saveRule} />}
    </Card>
  )
}
