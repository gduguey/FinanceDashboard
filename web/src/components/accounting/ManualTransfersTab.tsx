import { TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { toast } from 'sonner'
import { LinkedTransactionsTable } from '@/components/accounting/LinkedTransactionsTable'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useRemoveTransferLink } from '@/hooks/useAccountingData'
import {
  type LinkedPairRow,
  linkedPairRowFromLink,
  TRANSFER_UNLINK_WARNING_PAIR,
  type TransferRowInfo,
} from '@/lib/transferRowInfo'
import type { TransferLink } from '@/types/accounting'

// Every transfer link a user made directly, rather than one a rule matched
// — `TransferRulesTab`'s own "linked by this rule" table groups the
// rule-made links by rule; this is the flat counterpart for the ones with
// no rule involved at all (`rule_id == null`), reusing the exact same
// virtualized/sortable/no-horizontal-scroll table so both tables look and
// behave identically.
export function ManualTransfersTab({
  transferLinks,
  legByTransactionId,
}: {
  transferLinks: TransferLink[]
  legByTransactionId: Map<string, TransferRowInfo>
}) {
  const removeTransferLink = useRemoveTransferLink()
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
        <CardDescription>
          {rows.length} manually added {rows.length === 1 ? 'transfer' : 'transfers'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <LinkedTransactionsTable
          rows={rows}
          emptyMessage="No transfers have been manually added."
          renderRowAction={(row) => (
            <div className="flex items-center gap-2">
              <Tooltip>
                <TooltipTrigger className="inline-flex text-muted-foreground/70 hover:text-foreground">
                  <TriangleAlert className="size-3.5" />
                </TooltipTrigger>
                <TooltipContent className="max-w-64 text-pretty">
                  <p>{TRANSFER_UNLINK_WARNING_PAIR}</p>
                </TooltipContent>
              </Tooltip>
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
