import { ArrowDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import { TransferRowCard } from '@/components/accounting/TransferRowCard'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { TransferBadgeInfo } from '@/lib/transferBadges'
import { TRANSFER_UNLINK_WARNING_PAIR } from '@/lib/transferRowInfo'

// The popup a category-column transfer badge opens on click — exactly one
// of the three shapes `TransferBadgeInfo.popup` can hold. Its own component
// (rather than inline JSX) so each branch can pull the fields it needs into
// plain local consts once, instead of repeating `badge.popup.foo` property
// access inside click handlers (which TypeScript can't narrow the same way
// a local const's own type gets narrowed).
// Reads clearly above the button it warns about, rather than as a vague
// aside below it — spelled out concretely (re-linking is a manual redo,
// not a click away) instead of the ambiguous "there is no going back".
const UNMARK_WARNING_SINGLE =
  "This can't be undone automatically — you'd have to manually flag this transaction again if you change your mind."

export function TransferDetailDialog({
  badge,
  ruleLabelById,
  onClose,
  onUnlinkTransfer,
  onUndoManualOverride,
  onExcludeFromRule,
  onExcludeAndUnlinkFromRule,
}: {
  badge: TransferBadgeInfo
  ruleLabelById: Map<string, string | undefined>
  onClose: () => void
  onUnlinkTransfer: (linkId: string) => void
  onUndoManualOverride: (postingId: string) => void
  onExcludeFromRule: (transactionIds: string[], ruleId: string) => void
  onExcludeAndUnlinkFromRule: (transactionIds: string[], ruleId: string, linkId: string) => void
}) {
  const { popup } = badge
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Transfer</DialogTitle>
        </DialogHeader>
        {popup.kind === 'override' ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes into the external account <span className="font-medium">{popup.otherAccountName}</span>
              .
            </p>
            <p className="text-xs text-muted-foreground">{UNMARK_WARNING_SINGLE}</p>
            <Button
              variant="destructive"
              onClick={() => {
                onUndoManualOverride(popup.postingId)
                onClose()
              }}
            >
              Unmark as transfer
            </Button>
          </div>
        ) : popup.kind === 'direct-rule' ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes from <span className="font-medium">{popup.from.accountName}</span> to{' '}
              <span className="font-medium">{popup.to.accountName}</span>, and is part of rule{' '}
              <Link className="underline hover:text-foreground" to={`/rules?tab=rules&ruleId=${popup.ruleId}`}>
                {ruleLabelById.get(popup.ruleId) ?? popup.ruleId}
              </Link>
              .
            </p>
            <div className="flex flex-col items-stretch gap-1">
              <TransferRowCard row={popup.from} />
              <ArrowDown className="mx-auto size-4 shrink-0 text-muted-foreground" />
              <TransferRowCard row={popup.to} />
            </div>
            <Button
              variant="destructive"
              onClick={() => {
                onExcludeFromRule([popup.transactionId], popup.ruleId)
                onClose()
              }}
            >
              Exclude this specific transfer from rule {ruleLabelById.get(popup.ruleId) ?? popup.ruleId}
            </Button>
            <p className="text-xs text-muted-foreground">
              The excluded transfers can be found{' '}
              <Link className="underline hover:text-foreground" to={`/rules?tab=excluded&ruleId=${popup.ruleId}`}>
                here
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              This transfer goes from <span className="font-medium">{popup.from.accountName}</span> to{' '}
              <span className="font-medium">{popup.to.accountName}</span>
              {popup.source === 'rule' && popup.ruleId
                ? (() => {
                    const ruleId = popup.ruleId
                    return (
                      <>
                        {' '}
                        and is part of rule{' '}
                        <Link className="underline hover:text-foreground" to={`/rules?tab=rules&ruleId=${ruleId}`}>
                          {ruleLabelById.get(ruleId) ?? ruleId}
                        </Link>
                      </>
                    )
                  })()
                : null}
              .
            </p>
            <div className="flex flex-col items-stretch gap-1">
              <TransferRowCard row={popup.from} />
              <ArrowDown className="mx-auto size-4 shrink-0 text-muted-foreground" />
              <TransferRowCard row={popup.to} />
            </div>
            {popup.source === 'rule' && popup.ruleId ? (
              (() => {
                const ruleId = popup.ruleId
                const fromTransactionId = popup.from.transactionId
                const toTransactionId = popup.to.transactionId
                return (
                  <>
                    <Button
                      variant="destructive"
                      onClick={() => {
                        onExcludeAndUnlinkFromRule([fromTransactionId, toTransactionId], ruleId, popup.linkId)
                        onClose()
                      }}
                    >
                      Exclude this specific transfer from rule {ruleLabelById.get(ruleId) ?? ruleId}
                    </Button>
                    <p className="text-xs text-muted-foreground">
                      Both transactions go back to being normal transactions. The excluded transfers can be found{' '}
                      <Link className="underline hover:text-foreground" to={`/rules?tab=excluded&ruleId=${ruleId}`}>
                        here
                      </Link>
                      .
                    </p>
                  </>
                )
              })()
            ) : (
              <>
                <p className="text-xs text-muted-foreground">{TRANSFER_UNLINK_WARNING_PAIR}</p>
                <Button
                  variant="destructive"
                  onClick={() => {
                    onUnlinkTransfer(popup.linkId)
                    onClose()
                  }}
                >
                  Unmark as transfer
                </Button>
              </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
