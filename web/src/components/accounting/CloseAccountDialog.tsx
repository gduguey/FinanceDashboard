import { Plus, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useRatesToBase } from '@/hooks/useAccountingData'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency } from '@/lib/format'
import type { Account, ManualTransfer } from '@/types/accounting'

const ZERO_TOLERANCE = 0.005

interface SplitRow {
  key: string
  otherAccountId: string
  ownAmount: string
  otherAmount: string
}

// The remaining balance moves out of the closing account when it's
// positive (money to relocate), or into it when it's negative (a debt
// being paid off from elsewhere) — `ownAmount` on every row is always
// denominated in the closing account's own currency either way.
export function CloseAccountDialog({
  account,
  balance,
  otherAccounts,
  onClose,
  onConfirm,
  isSubmitting,
}: {
  account: Account
  balance: number
  otherAccounts: Account[]
  onClose: () => void
  onConfirm: (transfers: ManualTransfer[]) => void
  isSubmitting: boolean
}) {
  const magnitude = Math.abs(balance)
  const hasBalance = magnitude > ZERO_TOLERANCE
  const movingOut = balance > 0
  const ratesToBase = useRatesToBase([account.currency, ...otherAccounts.map((other) => other.currency)])
  const [rows, setRows] = useState<SplitRow[]>(
    hasBalance
      ? [{ key: 'row-0', otherAccountId: '', ownAmount: magnitude.toFixed(2), otherAmount: magnitude.toFixed(2) }]
      : [],
  )
  const [skipTransfer, setSkipTransfer] = useState(false)
  // A stable, ever-increasing counter — not `rows.length`, which collides
  // after a remove-then-add (e.g. removing "row-0" from a 2-row array then
  // adding a new one would regenerate "row-1" again).
  const nextRowIndex = useRef(1)
  // `Account` has no `closed_at` of its own (see `models.Account.closed`) —
  // this only dates the balance-moving transfer(s) recorded below, the one
  // artifact a close actually produces, defaulting to today.
  const [closingDate, setClosingDate] = useState(() => new Date().toISOString().slice(0, 10))

  const accountItems = Object.fromEntries(
    otherAccounts.map((other) => [other.account_id, `${other.name} (${other.currency})`]),
  )

  function otherAmountFor(otherAccountId: string, ownAmount: string): string {
    const other = otherAccounts.find((candidate) => candidate.account_id === otherAccountId)
    if (!other || other.currency === account.currency) return ownAmount
    const parsed = Number.parseFloat(ownAmount)
    if (Number.isNaN(parsed)) return ''
    return convertCurrency(parsed, account.currency, other.currency, ratesToBase).toFixed(2)
  }

  function updateRow(key: string, patch: Partial<Pick<SplitRow, 'otherAccountId' | 'ownAmount'>>) {
    setRows((prev) =>
      prev.map((row) => {
        if (row.key !== key) return row
        const next = { ...row, ...patch }
        if (patch.otherAccountId !== undefined || patch.ownAmount !== undefined) {
          next.otherAmount = otherAmountFor(next.otherAccountId, next.ownAmount)
        }
        return next
      }),
    )
  }

  function addRow() {
    const key = `row-${nextRowIndex.current++}`
    setRows((prev) => [...prev, { key, otherAccountId: '', ownAmount: '', otherAmount: '' }])
  }

  function removeRow(key: string) {
    setRows((prev) => prev.filter((row) => row.key !== key))
  }

  const allocated = rows.reduce((sum, row) => sum + (Number.parseFloat(row.ownAmount) || 0), 0)
  const remaining = magnitude - allocated
  const isBalanced = Math.abs(remaining) < ZERO_TOLERANCE
  const rowsValid = rows.every(
    (row) => row.otherAccountId && Number.parseFloat(row.ownAmount) > 0 && Number.parseFloat(row.otherAmount) > 0,
  )
  const canConfirm = !hasBalance || skipTransfer || (rows.length > 0 && rowsValid && isBalanced)

  function handleConfirm() {
    if (!hasBalance || skipTransfer) {
      onConfirm([])
      return
    }
    const transfers: ManualTransfer[] = rows.map((row, index) => {
      const ownAmount = Number.parseFloat(row.ownAmount)
      const otherAmount = Number.parseFloat(row.otherAmount)
      return {
        transfer_id: `close:${account.account_id}:${index}:${row.key}`,
        date: closingDate,
        from_account_id: movingOut ? account.account_id : row.otherAccountId,
        to_account_id: movingOut ? row.otherAccountId : account.account_id,
        from_amount: movingOut ? ownAmount : otherAmount,
        to_amount: movingOut ? otherAmount : ownAmount,
        description: `Closing ${account.name}`,
      }
    })
    onConfirm(transfers)
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Close {account.name}</DialogTitle>
        </DialogHeader>

        <Field label="Closing date" className="w-40">
          {(id) => (
            <Input id={id} type="date" value={closingDate} onChange={(event) => setClosingDate(event.target.value)} />
          )}
        </Field>

        {!hasBalance ? (
          <p className="text-sm text-muted-foreground">
            This account has a {formatCurrency(0, account.currency)} balance — closing it keeps its full transaction
            history, it just stops appearing as a destination for new imports or transfers.
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              This account still has a balance of {formatCurrency(balance, account.currency)}.{' '}
              {movingOut
                ? "Record where it's moving to before closing:"
                : 'Record where the payoff is coming from before closing:'}
            </p>
            <div className="space-y-2">
              {rows.map((row) => {
                const other = otherAccounts.find((candidate) => candidate.account_id === row.otherAccountId)
                const crossCurrency = other && other.currency !== account.currency
                return (
                  <div key={row.key} className="flex flex-wrap items-end gap-2">
                    <Field label={movingOut ? 'To account' : 'From account'} className="min-w-48 flex-1">
                      {(id) => (
                        <Select
                          value={row.otherAccountId}
                          onValueChange={(value) => value && updateRow(row.key, { otherAccountId: value })}
                        >
                          <SelectTrigger id={id} size="sm" className="w-full">
                            <SelectValue placeholder="Choose an account…" items={accountItems} />
                          </SelectTrigger>
                          <SelectContent>
                            {otherAccounts.map((candidate) => (
                              <SelectItem key={candidate.account_id} value={candidate.account_id}>
                                {candidate.name} ({candidate.currency})
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </Field>
                    <Field label={<>Amount ({account.currency})</>} className="w-32">
                      {(id) => (
                        <Input
                          id={id}
                          type="number"
                          inputMode="decimal"
                          className="text-right"
                          value={row.ownAmount}
                          onChange={(event) => updateRow(row.key, { ownAmount: event.target.value })}
                        />
                      )}
                    </Field>
                    {crossCurrency && (
                      <Field label={<>Received ({other.currency})</>} className="w-32">
                        {(id) => (
                          <Input
                            id={id}
                            type="number"
                            inputMode="decimal"
                            className="text-right"
                            value={row.otherAmount}
                            onChange={(event) =>
                              setRows((prev) =>
                                prev.map((candidate) =>
                                  candidate.key === row.key
                                    ? { ...candidate, otherAmount: event.target.value }
                                    : candidate,
                                ),
                              )
                            }
                          />
                        )}
                      </Field>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => removeRow(row.key)}
                      title="Remove this split"
                      // The row's own identity is whichever account it points
                      // at, which the user may not have chosen yet — so the
                      // name falls back to the position in the list rather
                      // than saying nothing at all.
                      aria-label={other ? `Remove the split to ${other.name}` : `Remove split ${rows.indexOf(row) + 1}`}
                    >
                      <Trash2 className="size-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                )
              })}
            </div>
            <Button variant="outline" size="sm" onClick={addRow}>
              <Plus className="size-3.5" /> Add another account
            </Button>
            <p className={`text-xs ${isBalanced ? 'text-muted-foreground' : 'text-destructive'}`}>
              {isBalanced
                ? 'Fully allocated.'
                : `Remaining to allocate: ${formatCurrency(remaining, account.currency)}`}
            </p>
            {/* Row-reversed because `Field` renders its caption first, and here the switch sits ahead of the text. */}
            <Field
              label="Close without recording a transfer"
              className="flex-row-reverse items-center justify-end gap-2"
            >
              {(id) => <Switch id={id} size="sm" checked={skipTransfer} onCheckedChange={setSkipTransfer} />}
            </Field>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button disabled={!canConfirm || isSubmitting} onClick={handleConfirm}>
            Close account
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
