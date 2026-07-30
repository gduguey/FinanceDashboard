import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useSetPostingSplit } from '@/hooks/useAccountingData'
import type { DraftLeg } from '@/lib/draftLegs'
import { newDraftLeg } from '@/lib/draftLegs'
import { formatCurrency } from '@/lib/format'
import type { Category, Posting } from '@/types/accounting'

const AMOUNT_TOLERANCE = 0.005

// A paycheck-shaped deposit landing as one bank posting can really be wage
// plus a reimbursement — two different things that happen to have arrived
// together. This turns one posting into several independently-categorized
// legs that still sum to the original amount (see `apply_posting_splits`).
export function PostingSplitDialog({
  posting,
  categories,
  onClose,
}: {
  posting: Posting
  categories: Record<string, Category>
  onClose: () => void
}) {
  // Lazy initializer, not a bare array literal: `newDraftLeg` advances a
  // module counter, and an inline literal would mint (and discard) a fresh
  // pair of ids on every single render of an open dialog.
  const [legs, setLegs] = useState<DraftLeg[]>(() => [
    newDraftLeg({
      amount: String(posting.amount),
      categoryId: posting.category_id ?? null,
      subcategoryId: posting.subcategory_id ?? null,
      description: posting.description,
    }),
    newDraftLeg({ amount: '0', categoryId: null, subcategoryId: null, description: '' }),
  ])
  const setSplit = useSetPostingSplit()
  const classification = posting.amount >= 0 ? 'income' : 'expense'

  const total = legs.reduce((sum, leg) => sum + (Number.parseFloat(leg.amount) || 0), 0)
  const remaining = posting.amount - total
  const canSave = Math.abs(remaining) < AMOUNT_TOLERANCE && legs.length >= 2

  function updateLeg(index: number, patch: Partial<DraftLeg>) {
    setLegs(legs.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)))
  }

  function addLeg() {
    setLegs([
      ...legs,
      newDraftLeg({ amount: remaining.toFixed(2), categoryId: null, subcategoryId: null, description: '' }),
    ])
  }

  function removeLeg(index: number) {
    setLegs(legs.filter((_, i) => i !== index))
  }

  async function handleSave() {
    try {
      await setSplit.mutateAsync({
        postingId: posting.posting_id,
        legs: legs.map((leg) => ({
          amount: Number.parseFloat(leg.amount) || 0,
          category_id: leg.categoryId,
          subcategory_id: leg.subcategoryId,
          description: leg.description,
        })),
      })
      onClose()
    } catch {
      // Already surfaced via the global mutation-error toast (see App.tsx) —
      // caught here only so a failed save doesn't also close the dialog or
      // throw as an unhandled promise rejection.
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Split transaction</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {posting.description} — {formatCurrency(posting.amount, posting.currency)}
        </p>
        <div className="space-y-2">
          {legs.map((leg, index) => (
            <div key={leg.id} className="flex items-end gap-2">
              <Input
                type="number"
                className="w-24"
                value={leg.amount}
                onChange={(event) => updateLeg(index, { amount: event.target.value })}
              />
              <CategorySelect
                categories={categories}
                classification={classification}
                value={leg.categoryId}
                onChange={(categoryId) => updateLeg(index, { categoryId, subcategoryId: null })}
              />
              <SubcategorySelect
                categories={categories}
                categoryId={leg.categoryId}
                value={leg.subcategoryId}
                onChange={(subcategoryId) => updateLeg(index, { subcategoryId })}
              />
              <Input
                className="flex-1"
                placeholder="Description"
                value={leg.description}
                onChange={(event) => updateLeg(index, { description: event.target.value })}
              />
              {legs.length > 2 && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove leg ${index + 1}`}
                  onClick={() => removeLeg(index)}
                >
                  <Trash2 className="size-3.5 text-muted-foreground" />
                </Button>
              )}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between text-sm">
          <Button variant="outline" size="sm" onClick={addLeg}>
            + Add leg
          </Button>
          <span className={Math.abs(remaining) > AMOUNT_TOLERANCE ? 'text-destructive' : 'text-muted-foreground'}>
            Remaining: {formatCurrency(remaining, posting.currency)}
          </span>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSave || setSplit.isPending} onClick={handleSave}>
            Save split
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
