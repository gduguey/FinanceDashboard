import { useMemo } from 'react'
import { toast } from 'sonner'
import { LinkedTransactionsTable } from '@/components/accounting/LinkedTransactionsTable'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useRemoveTransferLink } from '@/hooks/useAccountingData'
import {
  type LinkedPairRow,
  linkedPairRowFromLink,
  realLegByTransactionId,
  TRANSFER_UNLINK_WARNING_PAIR,
} from '@/lib/transferRowInfo'
import type { Account, Posting, TransferLink } from '@/types/accounting'

// Every transfer link a user made directly, rather than one a rule matched
// — `TransferRulesTab`'s own "linked by this rule" table groups the
// rule-made links by rule; this is the flat counterpart for the ones with
// no rule involved at all (`rule_id == null`), reusing the exact same
// virtualized/sortable/no-horizontal-scroll table so both tables look and
// behave identically.
export function ManualTransfersTab({
  transferLinks,
  accounts,
  postings,
}: {
  transferLinks: TransferLink[]
  accounts: Record<string, Account>
  postings: Posting[]
}) {
  const removeTransferLink = useRemoveTransferLink()
  const legByTransactionId = useMemo(() => realLegByTransactionId(postings, accounts), [postings, accounts])
  const rows = useMemo(() => {
    const result: LinkedPairRow[] = []
    for (const link of transferLinks) {
      if (link.rule_id) continue
      const row = linkedPairRowFromLink(link, legByTransactionId)
      if (row) result.push(row)
    }
    return result
  }, [transferLinks, legByTransactionId])

  function deleteTransfer(linkId: string) {
    removeTransferLink.mutate(linkId, {
      onSuccess: () => toast.success('Transfer deleted — both transactions are back to being normal transactions.'),
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Manually added transfers</CardTitle>
      </CardHeader>
      <CardContent>
        <LinkedTransactionsTable
          rows={rows}
          emptyMessage="No transfers have been manually added."
          renderRowAction={(row) => (
            <div className="flex max-w-xs flex-col items-end gap-1">
              <p className="text-right text-xs text-muted-foreground">{TRANSFER_UNLINK_WARNING_PAIR}</p>
              <Button variant="destructive" size="sm" onClick={() => deleteTransfer(row.linkId)}>
                Delete transfer
              </Button>
            </div>
          )}
        />
      </CardContent>
    </Card>
  )
}
