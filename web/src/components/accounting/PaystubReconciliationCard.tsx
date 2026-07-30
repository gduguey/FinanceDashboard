import { CheckCircle2, Trash2, Upload, XCircle } from 'lucide-react'
import { useState } from 'react'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { Truncate } from '@/components/shared/Truncate'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  useAccountingStore,
  useImportPaystub,
  useSetPostingOverride,
  useSetPostingSplit,
} from '@/hooks/useAccountingData'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { PaystubReconciliationResult, ProposedSplit, ProposedSplitLeg } from '@/types/accounting'

const AMOUNT_TOLERANCE = 0.005

interface DraftLeg {
  amount: string
  categoryId: string | null
  subcategoryId: string | null
  description: string
}

function toDraftLegs(legs: ProposedSplitLeg[]): DraftLeg[] {
  return legs.map((leg) => ({
    amount: String(leg.amount),
    categoryId: leg.category_id ?? null,
    subcategoryId: leg.subcategory_id ?? null,
    description: leg.description,
  }))
}

// Renders one matched deposit's proposed split (salary vs. reimbursement
// legs), editable before the user commits to it. Applying calls the same
// endpoints the Transactions split dialog uses — a single-leg proposal
// just overrides the posting's category, since `PostingSplit` requires at
// least two legs.
function ProposedSplitEditor({ proposal }: { proposal: ProposedSplit }) {
  const store = useAccountingStore()
  const [legs, setLegs] = useState<DraftLeg[]>(() => toDraftLegs(proposal.legs))
  const [applied, setApplied] = useState(false)
  const setSplit = useSetPostingSplit()
  const setOverride = useSetPostingOverride()

  const depositAmount = proposal.legs.reduce((sum, leg) => sum + leg.amount, 0)
  const total = legs.reduce((sum, leg) => sum + (Number.parseFloat(leg.amount) || 0), 0)
  const remaining = depositAmount - total
  const canApply = Math.abs(remaining) < AMOUNT_TOLERANCE && legs.length >= 1

  function updateLeg(index: number, patch: Partial<DraftLeg>) {
    setLegs(legs.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)))
  }

  function addLeg() {
    setLegs([...legs, { amount: remaining.toFixed(2), categoryId: null, subcategoryId: null, description: '' }])
  }

  function removeLeg(index: number) {
    setLegs(legs.filter((_, i) => i !== index))
  }

  async function handleApply() {
    if (legs.length >= 2) {
      await setSplit.mutateAsync({
        postingId: proposal.posting_id,
        legs: legs.map((leg) => ({
          amount: Number.parseFloat(leg.amount) || 0,
          category_id: leg.categoryId,
          subcategory_id: leg.subcategoryId,
          description: leg.description,
        })),
      })
    } else {
      await setOverride.mutateAsync({
        postingId: proposal.posting_id,
        override: { category_id: legs[0].categoryId, subcategory_id: legs[0].subcategoryId },
      })
    }
    setApplied(true)
  }

  if (!store.data) return null
  const isPending = setSplit.isPending || setOverride.isPending

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="space-y-2">
        {legs.map((leg, index) => (
          <div key={index} className="flex items-end gap-2">
            <Input
              type="number"
              className="w-24"
              disabled={applied}
              value={leg.amount}
              onChange={(event) => updateLeg(index, { amount: event.target.value })}
            />
            <CategorySelect
              categories={store.data.categories}
              classification="income"
              value={leg.categoryId}
              onChange={(categoryId) => updateLeg(index, { categoryId, subcategoryId: null })}
            />
            <SubcategorySelect
              categories={store.data.categories}
              categoryId={leg.categoryId}
              value={leg.subcategoryId}
              onChange={(subcategoryId) => updateLeg(index, { subcategoryId })}
            />
            <Input
              className="flex-1"
              placeholder="Description"
              disabled={applied}
              value={leg.description}
              onChange={(event) => updateLeg(index, { description: event.target.value })}
            />
            {legs.length > 1 && !applied && (
              <Button variant="ghost" size="icon" onClick={() => removeLeg(index)}>
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between text-sm">
        <Button variant="outline" size="sm" disabled={applied} onClick={addLeg}>
          + Add leg
        </Button>
        <div className="flex items-center gap-3">
          <span className={Math.abs(remaining) > AMOUNT_TOLERANCE ? 'text-destructive' : 'text-muted-foreground'}>
            Remaining: {formatCurrency(remaining, 'USD')}
          </span>
          {applied ? (
            <span className="flex items-center gap-1 text-emerald-600">
              <CheckCircle2 className="size-3.5" /> Applied
            </span>
          ) : (
            <Button size="sm" disabled={!canApply || isPending} onClick={handleApply}>
              Apply
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

interface UploadEntry {
  id: string
  fileName: string
  status: 'pending' | 'done' | 'error'
  result: PaystubReconciliationResult | null
  error: string | null
}

function ReconciliationResultView({ result }: { result: PaystubReconciliationResult }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Gross {formatCurrency(result.statement.gross_pay, 'USD')} · Taxes{' '}
        {formatCurrency(result.statement.taxes_withheld, 'USD')} · Net {formatCurrency(result.statement.net_pay, 'USD')}
      </p>
      <ul className="space-y-1 text-sm">
        {result.matches.map((match, index) => (
          <li key={index} className="flex items-center gap-2">
            {match.posting_id ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />
            ) : (
              <XCircle className="size-3.5 shrink-0 text-destructive" />
            )}
            <span>
              {match.label} — {formatCurrency(match.amount, 'USD')}
            </span>
            <span className="text-muted-foreground">
              {match.posting_id ? 'matched to a bank posting' : 'no matching bank posting found'}
            </span>
          </li>
        ))}
      </ul>

      {result.proposed_splits.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Proposed categorization</p>
          {result.proposed_splits.map((proposal) => (
            <ProposedSplitEditor key={proposal.posting_id} proposal={proposal} />
          ))}
        </div>
      )}
    </div>
  )
}

// Read-only parse + reconciliation, but `proposed_splits` lets the user
// review, edit, and apply a categorization for each matched deposit right
// here — applying calls the same split/override endpoints Transactions
// uses, so nothing about a posting changes until the user clicks Apply.
//
// Accepts several PDFs at once (drag-and-drop, or multi-select from the
// file picker) since one paystub is often accompanied by others from past
// months — each is parsed and reconciled independently, so one bad/unknown
// layout in the batch doesn't block the rest.
export function PaystubReconciliationCard() {
  const importPaystub = useImportPaystub()
  const [uploads, setUploads] = useState<UploadEntry[]>([])
  const [isDragging, setIsDragging] = useState(false)

  async function handleFiles(files: File[]) {
    const pdfFiles = files.filter((file) => file.name.toLowerCase().endsWith('.pdf'))
    if (pdfFiles.length === 0) return

    const entries: UploadEntry[] = pdfFiles.map((file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      fileName: file.name,
      status: 'pending',
      result: null,
      error: null,
    }))
    setUploads((prev) => [...entries, ...prev])

    // Sequential, not parallel — each call hits the same PDF-text-extraction
    // + ledger-matching path, and keeping it one-at-a-time makes per-file
    // progress easy to show without juggling concurrent request state.
    for (const [index, file] of pdfFiles.entries()) {
      const entryId = entries[index].id
      try {
        const result = await importPaystub.mutateAsync(file)
        setUploads((prev) => prev.map((entry) => (entry.id === entryId ? { ...entry, status: 'done', result } : entry)))
      } catch (error) {
        setUploads((prev) =>
          prev.map((entry) =>
            entry.id === entryId
              ? {
                  ...entry,
                  status: 'error',
                  error: error instanceof Error ? error.message : 'Could not parse this paystub',
                }
              : entry,
          ),
        )
      }
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reconcile a paystub</CardTitle>
        <CardDescription>
          Upload one or more paystub PDFs to check their deposits against real bank postings near pay day, and review a
          proposed salary/reimbursement split for each matched deposit before applying it. Paystub layouts vary a lot by
          payroll provider, so this may need its parsing patterns adjusted for yours (see{' '}
          <code>accounting/importers/paystub.py</code>).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* The drop target is pointer-only by nature; the "choose files" label
            below wraps a real file input, so keyboard users reach the same
            action there. */}
        <div
          role="none"
          className={cn(
            'flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-6 text-center transition-colors',
            isDragging ? 'border-primary bg-primary/5' : 'border-muted-foreground/25',
          )}
          onDragOver={(event) => {
            event.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setIsDragging(false)
            void handleFiles(Array.from(event.dataTransfer.files))
          }}
        >
          <Upload className="size-5 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Drag and drop paystub PDFs here, or</p>
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-primary underline-offset-4 hover:underline">
            choose files
            <input
              type="file"
              accept=".pdf,.PDF"
              multiple
              className="hidden"
              onChange={(event) => {
                void handleFiles(Array.from(event.target.files ?? []))
                event.target.value = ''
              }}
            />
          </label>
        </div>

        {uploads.map((entry) => (
          <div key={entry.id} className="space-y-2 rounded-md border p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              {entry.status === 'done' && <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />}
              {entry.status === 'error' && <XCircle className="size-3.5 shrink-0 text-destructive" />}
              <Truncate text={entry.fileName} />
            </div>
            {entry.status === 'pending' && (
              <LoadingProgressBar step="Extracting the paystub's text and matching it against the ledger…" />
            )}
            {entry.status === 'error' && <p className="text-sm text-destructive">{entry.error}</p>}
            {entry.status === 'done' && entry.result && <ReconciliationResultView result={entry.result} />}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
