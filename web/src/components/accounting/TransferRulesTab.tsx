import { ChevronDown, ChevronRight, Pencil, Trash2 } from 'lucide-react'
import { Fragment, useState } from 'react'
import { toast } from 'sonner'
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
import { useCreateTransferRule, useSetTransferRules } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { counterpartyOptions, needsLinkingAccount } from '@/lib/counterpartyAccounts'
import { formatCurrency, formatDate } from '@/lib/format'
import type { Account, Posting, TransferRule } from '@/types/accounting'

const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

interface ExcludedTransactionInfo {
  transactionId: string
  description: string
  postedAt: string | null
  amount: number | null
  currency: string
}

// A rule's `excluded_transaction_ids` names transactions, never postings —
// this looks up each one's own real (non-placeholder) leg purely to show a
// person something recognizable (date/description/amount) instead of a raw
// id. A transaction id with no matching posting anymore (its statement was
// re-imported away, or the ledger was rebuilt) still gets a row, just with
// blanks instead of a crash.
function excludedTransactionInfos(postings: Posting[], transactionIds: string[]): ExcludedTransactionInfo[] {
  const realLegByTransactionId = new Map<string, Posting>()
  for (const posting of postings) {
    if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) continue
    if (!realLegByTransactionId.has(posting.transaction_id)) realLegByTransactionId.set(posting.transaction_id, posting)
  }
  return transactionIds.map((transactionId) => {
    const posting = realLegByTransactionId.get(transactionId)
    return {
      transactionId,
      description: posting?.description ?? transactionId,
      postedAt: posting?.posted_at ?? null,
      amount: posting?.amount ?? null,
      currency: posting?.currency ?? 'USD',
    }
  })
}

// Shown under the counterparty picker whenever the chosen account is one a
// rule can't safely repoint straight onto (see
// `lib.counterpartyAccounts.needsLinkingAccount`) — explains the actual
// behavior so it doesn't read as "nothing happened" when a match isn't
// found yet.
function NeedsLinkingNote() {
  return (
    <p className="max-w-sm text-xs text-muted-foreground">
      Matches will be linked to their counterpart transaction, not stamped directly — if no match exists yet, the
      transaction stays uncategorized until one is imported.
    </p>
  )
}

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
          {needsLinkingAccount(accounts.find((account) => account.account_id === draft.counterparty_account_id)) && (
            <NeedsLinkingNote />
          )}
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

export function TransferRulesTab({
  rules,
  accounts,
  postings,
}: {
  rules: TransferRule[]
  accounts: Record<string, Account>
  postings: Posting[]
}) {
  const setRules = useSetTransferRules()
  const createRule = useCreateTransferRule()
  const [editing, setEditing] = useState<TransferRule | null>(null)
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(null)
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
    createRule.mutate({
      description_contains: draft.descriptionContains,
      account_id: null,
      counterparty_account_id: draft.counterpartyAccountId,
      priority: 100,
      description: '',
    })
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

  // Restores a rule's effect for one specific transaction — it either falls
  // back to the next-matching rule, or stays uncategorized if none matches,
  // the same choice excluding it made in the first place (see
  // TransactionsTab.tsx's own onExcludeFromRule).
  function removeExclusion(ruleId: string, transactionId: string) {
    const updated = rules.map((rule) =>
      rule.rule_id === ruleId
        ? {
            ...rule,
            excluded_transaction_ids: (rule.excluded_transaction_ids ?? []).filter((id) => id !== transactionId),
          }
        : rule,
    )
    setRules.mutate(updated, {
      onSuccess: () =>
        toast.success('Exclusion removed — this rule will resolve that transaction again, if it still matches.'),
    })
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
              <TableHead>Exclusions</TableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-6 text-center text-sm text-muted-foreground">
                  No rules yet — add one below to automatically categorize recurring transfers.
                </TableCell>
              </TableRow>
            )}
            {sorted.map((rule) => {
              const excludedIds = rule.excluded_transaction_ids ?? []
              const expanded = expandedRuleId === rule.rule_id
              return (
                <Fragment key={rule.rule_id}>
                  <TableRow className={rule.active ? '' : 'opacity-50'}>
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
                    <TableCell>
                      {excludedIds.length > 0 ? (
                        <button
                          type="button"
                          className="inline-flex items-center gap-0.5 rounded-sm px-1 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                          title="Show which transactions this rule is excluded from"
                          onClick={() => setExpandedRuleId(expanded ? null : rule.rule_id)}
                        >
                          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                          {excludedIds.length} excluded
                        </button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
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
                  {expanded && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={7}>
                        <div className="flex flex-col gap-1 py-1">
                          <p className="text-xs text-muted-foreground">
                            These transactions are excluded from this rule specifically — each falls back to the
                            next-matching rule, or stays uncategorized if none matches. Everything else this rule
                            matches is unaffected.
                          </p>
                          {excludedTransactionInfos(postings, excludedIds).map((info) => (
                            <div
                              key={info.transactionId}
                              className="flex items-center justify-between gap-2 rounded-sm border bg-background px-2 py-1 text-xs"
                            >
                              <span className="flex min-w-0 items-center gap-2">
                                <span className="truncate">{info.description}</span>
                                {info.postedAt && (
                                  <span className="shrink-0 text-muted-foreground">
                                    {formatDate(info.postedAt.slice(0, 10))}
                                  </span>
                                )}
                                {info.amount !== null && (
                                  <span className="shrink-0 tabular-nums text-muted-foreground">
                                    {formatCurrency(info.amount, info.currency)}
                                  </span>
                                )}
                              </span>
                              <Button
                                variant="ghost"
                                size="icon"
                                title="Remove this exclusion — the rule will resolve this transaction again, if it still matches"
                                onClick={() => removeExclusion(rule.rule_id, info.transactionId)}
                              >
                                <Trash2 className="size-3.5 text-muted-foreground" />
                              </Button>
                            </div>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
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
        {needsLinkingAccount(options.find((account) => account.account_id === draft.counterpartyAccountId)) && (
          <NeedsLinkingNote />
        )}
      </CardContent>
      {editing && (
        <TransferRuleEditDialog rule={editing} accounts={options} onClose={() => setEditing(null)} onSave={saveRule} />
      )}
    </Card>
  )
}
