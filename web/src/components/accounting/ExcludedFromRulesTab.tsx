import { Fragment, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ExcludedTransactionsTable } from '@/components/accounting/ExcludedTransactionsTable'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useSetTransferRules } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import { realLegByTransactionId, type TransferRowInfo } from '@/lib/transferRowInfo'
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
  const setRules = useSetTransferRules()
  const rulesWithExclusions = useMemo(
    () => rules.filter((rule) => (rule.excluded_transaction_ids ?? []).length > 0),
    [rules],
  )
  const { sorted, sort, toggleSort } = useSortableRows(rulesWithExclusions, 'priority')
  const legByTransactionId = useMemo(() => realLegByTransactionId(postings, accounts), [postings, accounts])
  const [searchParams] = useSearchParams()
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(searchParams.get('ruleId'))

  // Restores a rule's effect for one specific transaction — it either falls
  // back to the next-matching rule, or stays uncategorized if none matches,
  // the same choice excluding it made in the first place.
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
              const excludedIds = rule.excluded_transaction_ids ?? []
              const expanded = expandedRuleId === rule.rule_id
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
                    <TableCell className="text-muted-foreground">{excludedIds.length}</TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={3} onClick={(event) => event.stopPropagation()}>
                        <div className="py-1">
                          <ExcludedTransactionsTable
                            rows={excludedRowsForRule(legByTransactionId, excludedIds)}
                            onRemoveExclusion={(transactionId) => removeExclusion(rule.rule_id, transactionId)}
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
      </CardContent>
    </Card>
  )
}
