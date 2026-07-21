import { Pencil, Trash2 } from 'lucide-react'
import { Fragment, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { LinkedTransactionsTable } from '@/components/accounting/LinkedTransactionsTable'
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
import {
  useCreateTransferRule,
  useDeleteTransferRule,
  usePatchTransferRule,
  useRemoveTransferLink,
} from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { counterpartyOptions, needsLinkingAccount } from '@/lib/counterpartyAccounts'
import { type LinkedPairRow, linkedPairRowFromLink, realLegByTransactionId } from '@/lib/transferRowInfo'
import { addedExcludedTransactionIds, ruleUpdateFromRule } from '@/lib/transferRules'
import type { Account, Posting, TransferLink, TransferRule } from '@/types/accounting'

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
  transferLinks,
}: {
  rules: TransferRule[]
  accounts: Record<string, Account>
  postings: Posting[]
  transferLinks: TransferLink[]
}) {
  const patchRule = usePatchTransferRule()
  const deleteRule = useDeleteTransferRule()
  const createRule = useCreateTransferRule()
  const removeTransferLink = useRemoveTransferLink()
  const [editing, setEditing] = useState<TransferRule | null>(null)
  // Which rule's "linked by this rule" table is open. Seeded from `?ruleId=`
  // so the transfer-detail popup's "part of rule…" link can jump straight
  // to it (see `TransactionsTab.tsx`'s `TransferDetailDialog`). Excluded
  // transactions have their own dedicated "Excluded from rules" tab
  // (`ExcludedFromRulesTab.tsx`) rather than a second expandable section here.
  const [searchParams] = useSearchParams()
  const [linkedExpandedRuleId, setLinkedExpandedRuleId] = useState<string | null>(searchParams.get('ruleId'))
  const [draft, setDraft] = useState<{ descriptionContains: string; counterpartyAccountId: string | null }>({
    descriptionContains: '',
    counterpartyAccountId: null,
  })
  const { sorted, sort, toggleSort } = useSortableRows(rules, 'priority')
  const options = counterpartyOptions(accounts)
  const counterpartyName = (rule: TransferRule) =>
    (rule.counterparty_account_id && accounts[rule.counterparty_account_id]?.name) || '—'
  const legByTransactionId = useMemo(() => realLegByTransactionId(postings, accounts), [postings, accounts])
  // Every confirmed transfer link, grouped by the rule that created it —
  // links a user made manually (`rule_id === null`) never show up here.
  const linkedPairsByRuleId = useMemo(() => {
    const map = new Map<string, LinkedPairRow[]>()
    for (const link of transferLinks) {
      if (!link.rule_id) continue
      const row = linkedPairRowFromLink(link, legByTransactionId)
      if (!row) continue
      const existing = map.get(link.rule_id)
      if (existing) existing.push(row)
      else map.set(link.rule_id, [row])
    }
    return map
  }, [transferLinks, legByTransactionId])

  // Excluding a rule-linked transfer both stops the rule from re-linking it
  // (via `excluded_transaction_ids`, same as the Excluded-from-rules tab's
  // "remove exclusion" is the inverse of) and drops the `TransferLink` it
  // already made — sequential, not parallel, since `removeTransferLink` goes
  // out against the whole-store version header, which only advances once
  // the rule patch's own success has refetched the store (see
  // `TransactionsTab.tsx`'s `handleExcludeAndUnlinkFromRule`, which this
  // mirrors for the Rules page's own "linked by this rule" table).
  async function excludeFromRule(rule: TransferRule, linkId: string, transactionIds: string[]) {
    const ruleLabel = rule.description || rule.description_contains || rule.rule_id
    await patchRule.mutateAsync({
      ruleId: rule.rule_id,
      update: ruleUpdateFromRule(rule, {
        excluded_transaction_ids: addedExcludedTransactionIds(rule, transactionIds),
      }),
    })
    await removeTransferLink.mutateAsync(linkId)
    toast.success(`Excluded from "${ruleLabel}" — both transactions are back to being normal transactions.`)
  }

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
    deleteRule.mutate(ruleId)
  }

  function saveRule(updated: TransferRule) {
    patchRule.mutate({ ruleId: updated.rule_id, update: ruleUpdateFromRule(updated) })
  }

  function toggleActive(ruleId: string, active: boolean) {
    const rule = rules.find((r) => r.rule_id === ruleId)
    if (!rule) return
    patchRule.mutate({ ruleId, update: ruleUpdateFromRule(rule, { active }) })
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
              <TableHead>Linked</TableHead>
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
              const linkedRows = linkedPairsByRuleId.get(rule.rule_id) ?? []
              const linkedExpanded = linkedExpandedRuleId === rule.rule_id
              return (
                <Fragment key={rule.rule_id}>
                  <TableRow
                    className={`cursor-pointer ${rule.active ? '' : 'opacity-50'}`}
                    aria-expanded={linkedExpanded}
                    onClick={() => setLinkedExpandedRuleId(linkedExpanded ? null : rule.rule_id)}
                  >
                    <TableCell className="font-medium">{rule.description_contains}</TableCell>
                    <TableCell className="text-muted-foreground">{counterpartyName(rule)}</TableCell>
                    <TableCell className="max-w-xs text-muted-foreground">
                      {rule.description ? <Truncate text={rule.description} /> : '—'}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{rule.priority}</TableCell>
                    <TableCell onClick={(event) => event.stopPropagation()}>
                      <Switch
                        size="sm"
                        checked={rule.active}
                        onCheckedChange={(checked) => toggleActive(rule.rule_id, checked)}
                      />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {linkedRows.length > 0 ? `${linkedRows.length} linked` : '—'}
                    </TableCell>
                    <TableCell className="flex gap-1" onClick={(event) => event.stopPropagation()}>
                      <Button variant="ghost" size="icon" onClick={() => setEditing(rule)}>
                        <Pencil className="size-3.5 text-muted-foreground" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => removeRule(rule.rule_id)}>
                        <Trash2 className="size-3.5 text-muted-foreground" />
                      </Button>
                    </TableCell>
                  </TableRow>
                  {linkedExpanded && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={7} onClick={(event) => event.stopPropagation()}>
                        <div className="py-1">
                          <LinkedTransactionsTable
                            rows={linkedRows}
                            renderRowAction={(row) => (
                              <Button
                                variant="destructive"
                                size="sm"
                                title="Exclude this transfer from the rule — both transactions go back to being normal transactions"
                                onClick={() =>
                                  excludeFromRule(rule, row.linkId, [row.fromTransactionId, row.toTransactionId])
                                }
                              >
                                Exclude from rule
                              </Button>
                            )}
                          />
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
