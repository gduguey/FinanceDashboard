import { Fragment, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ExcludedTransactionsTable } from '@/components/accounting/ExcludedTransactionsTable'
import { LinkedTransactionsTable } from '@/components/accounting/LinkedTransactionsTable'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { usePatchTransferRule } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import {
  type LinkedPairRow,
  pairTransferRows,
  realLegByTransactionId,
  type TransferRowInfo,
} from '@/lib/transferRowInfo'
import { ruleUpdateFromRule } from '@/lib/transferRules'
import type { Account, Posting, TransferRule } from '@/types/accounting'

// A rule's `excluded_transaction_ids` names transactions, never postings —
// this looks up each one's own real (non-placeholder) leg to show a person
// something recognizable (date/account/description/amount) instead of a raw
// id. A transaction id with no matching posting anymore (its statement was
// re-imported away, or the ledger was rebuilt) still gets a row, just with
// placeholder values instead of a crash.
function excludedRowsForRule(
  legByTransactionId: Map<string, TransferRowInfo>,
  transactionIds: string[],
): TransferRowInfo[] {
  return transactionIds.map(
    (transactionId) =>
      legByTransactionId.get(transactionId) ?? {
        transactionId,
        accountName: '—',
        description: transactionId,
        postedAt: new Date(0).toISOString(),
        amount: 0,
        currency: 'USD',
      },
  )
}

// Sibling to the Rules and Suggestions tabs — every rule that has at least
// one excluded transaction, with the same virtualized/sortable/click-to-expand
// table `TransferRulesTab`'s "linked by this rule" section uses, so the
// transfer-detail popup's "the excluded transfers can be found here" link
// (see `TransactionsTab.tsx`) always lands on a familiar layout.
export function ExcludedFromRulesTab({
  rules,
  accounts,
  postings,
}: {
  rules: TransferRule[]
  accounts: Record<string, Account>
  postings: Posting[]
}) {
  const patchRule = usePatchTransferRule()
  const rulesWithExclusions = useMemo(
    () => rules.filter((rule) => (rule.excluded_transaction_ids ?? []).length > 0),
    [rules],
  )
  const { sorted, sort, toggleSort } = useSortableRows(rulesWithExclusions, 'priority')
  const legByTransactionId = useMemo(() => realLegByTransactionId(postings, accounts), [postings, accounts])
  // Pairing is computed once per rule here (not just on expand) so the
  // "Excluded" count column below and the expanded detail tables always
  // agree on what counts as one transfer — a rule's `excluded_transaction_ids`
  // names transactions (1 transfer = 2 ids), so a raw `.length` overcounts
  // by roughly 2x for every rule whose exclusions paired up.
  const exclusionsByRuleId = useMemo(() => {
    const map = new Map<string, { pairs: LinkedPairRow[]; singles: TransferRowInfo[] }>()
    for (const rule of rulesWithExclusions) {
      map.set(
        rule.rule_id,
        pairTransferRows(excludedRowsForRule(legByTransactionId, rule.excluded_transaction_ids ?? [])),
      )
    }
    return map
  }, [rulesWithExclusions, legByTransactionId])
  const [searchParams] = useSearchParams()
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(searchParams.get('ruleId'))

  // Restores a rule's effect for one or both specific transactions — each
  // falls back to the next-matching rule, or stays uncategorized if none
  // matches, the same choice excluding it made in the first place. Takes
  // every id to restore in one call (both sides of a reconstructed pair)
  // so this rule's own `excluded_transaction_ids` only gets patched once,
  // not twice against the same starting `expected_version`.
  function removeExclusion(ruleId: string, transactionIds: string[]) {
    const rule = rules.find((r) => r.rule_id === ruleId)
    if (!rule) return
    const toRemove = new Set(transactionIds)
    patchRule.mutate(
      {
        ruleId,
        update: ruleUpdateFromRule(rule, {
          excluded_transaction_ids: (rule.excluded_transaction_ids ?? []).filter((id) => !toRemove.has(id)),
        }),
      },
      {
        onSuccess: () =>
          toast.success(
            `Exclusion removed — ${transactionIds.length > 1 ? 'both transactions' : 'this transaction'} will resolve again, if the rule still matches.`,
          ),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Excluded from rules</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'description_contains'}
                desc={sort.desc}
                onClick={() => toggleSort('description_contains')}
              >
                Rule
              </SortableTableHead>
              <TableHead>Counterparty</TableHead>
              <TableHead>Excluded</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} className="py-6 text-center text-sm text-muted-foreground">
                  No rule has any excluded transactions.
                </TableCell>
              </TableRow>
            )}
            {sorted.map((rule) => {
              const expanded = expandedRuleId === rule.rule_id
              const { pairs, singles } = exclusionsByRuleId.get(rule.rule_id) ?? { pairs: [], singles: [] }
              return (
                <Fragment key={rule.rule_id}>
                  <TableRow
                    className="cursor-pointer"
                    aria-expanded={expanded}
                    onClick={() => setExpandedRuleId(expanded ? null : rule.rule_id)}
                  >
                    <TableCell className="font-medium">{rule.description_contains}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {(rule.counterparty_account_id && accounts[rule.counterparty_account_id]?.name) || '—'}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{pairs.length + singles.length}</TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={3} onClick={(event) => event.stopPropagation()}>
                        <div className="flex flex-col gap-4 py-1">
                          {pairs.length > 0 && (
                            <LinkedTransactionsTable
                              rows={pairs}
                              emptyMessage="No excluded transfer pairs for this rule."
                              renderRowAction={(row) => (
                                <Button
                                  variant="destructive"
                                  size="sm"
                                  title="Remove this exclusion — the rule will try to re-link these transactions again, if it still matches"
                                  onClick={() =>
                                    removeExclusion(rule.rule_id, [row.fromTransactionId, row.toTransactionId])
                                  }
                                >
                                  Remove exclusion
                                </Button>
                              )}
                            />
                          )}
                          {singles.length > 0 && (
                            <ExcludedTransactionsTable
                              rows={singles}
                              onRemoveExclusion={(transactionId) => removeExclusion(rule.rule_id, [transactionId])}
                            />
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
