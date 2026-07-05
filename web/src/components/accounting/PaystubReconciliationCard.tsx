import { useState } from 'react'
import { CheckCircle2, Upload, XCircle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { formatCurrency } from '@/lib/format'
import { useImportPaystub } from '@/hooks/useAccountingData'
import type { PaystubReconciliationResult } from '@/types/accounting'

// Read-only: parses a paystub PDF and checks its deposits against real
// bank postings near pay day, but never applies a split itself — once a
// deposit is matched here, the user still uses the Split action on
// Transactions to actually break that posting into wage/reimbursement
// legs, since reconciling and committing to a specific split are two
// separate decisions.
export function PaystubReconciliationCard() {
  const importPaystub = useImportPaystub()
  const [result, setResult] = useState<PaystubReconciliationResult | null>(null)

  async function handleFile(file: File) {
    setResult(null)
    try {
      setResult(await importPaystub.mutateAsync(file))
    } catch {
      // surfaced below via importPaystub.error
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reconcile a paystub</CardTitle>
        <CardDescription>
          Upload a paystub PDF to check its deposits against real bank postings near pay day — read-only, applies
          nothing on its own. Paystub layouts vary a lot by payroll provider, so this may need its parsing patterns
          adjusted for yours (see <code>accounting/importers/paystub.py</code>).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-primary underline-offset-4 hover:underline">
          <Upload className="size-4" />
          Choose a paystub PDF
          <input
            type="file"
            accept=".pdf,.PDF"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void handleFile(file)
              event.target.value = ''
            }}
          />
        </label>

        {importPaystub.isPending && <LoadingProgressBar step="Extracting the paystub's text and matching it against the ledger…" />}

        {importPaystub.isError && (
          <p className="text-sm text-destructive">
            {importPaystub.error instanceof Error ? importPaystub.error.message : 'Could not parse this paystub'}
          </p>
        )}

        {result && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Gross {formatCurrency(result.statement.gross_pay, 'USD')} · Taxes{' '}
              {formatCurrency(result.statement.taxes_withheld, 'USD')} · Net{' '}
              {formatCurrency(result.statement.net_pay, 'USD')}
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
          </div>
        )}
      </CardContent>
    </Card>
  )
}
