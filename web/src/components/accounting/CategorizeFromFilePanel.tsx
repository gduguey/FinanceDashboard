import { useMemo, useState } from 'react'
import { CheckCircle2, Upload, X, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { formatCurrency, formatDate } from '@/lib/format'
import { useApplyCategorizeFromFile, useCategorizeFromFilePreview } from '@/hooks/useAccountingData'
import type { CategorizationMatch } from '@/types/accounting'

// One file at a time — unlike the multi-file drop in "Import statements",
// there's no batching win here: matching needs the whole ledger loaded
// once per file anyway, and reviewing several files' proposed matches
// together in one table would make it unclear which row came from which.
function FileDropZone({ onFile }: { onFile: (file: File) => void }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-6 py-14 text-center text-sm text-muted-foreground"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault()
        const file = event.dataTransfer.files[0]
        if (file) onFile(file)
      }}
    >
      <Upload className="size-6 text-muted-foreground/60" />
      Drag and drop a CSV or Excel file here
      <label className="cursor-pointer text-primary underline-offset-4 hover:underline">
        or browse
        <input
          type="file"
          accept=".csv,.CSV,.xlsx,.xls"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onFile(file)
            event.target.value = ''
          }}
        />
      </label>
    </div>
  )
}

function matchLabel(match: CategorizationMatch): string {
  if (!match.proposed_category_name) return '(no category in file)'
  return match.proposed_subcategory_name
    ? `${match.proposed_category_name} / ${match.proposed_subcategory_name}`
    : match.proposed_category_name
}

export function CategorizeFromFilePanel() {
  const [file, setFile] = useState<File | null>(null)
  const [checkedRows, setCheckedRows] = useState<Set<number>>(new Set())
  const preview = useCategorizeFromFilePreview()
  const apply = useApplyCategorizeFromFile()

  function handleFile(nextFile: File) {
    setFile(nextFile)
    apply.reset()
    preview.mutate(
      { file: nextFile },
      {
        onSuccess: (result) => {
          setCheckedRows(
            new Set(
              result.matches
                .filter((match) => match.posting_id !== null && match.proposed_category_id !== null)
                .map((match) => match.row_number),
            ),
          )
        },
      },
    )
  }

  function reset() {
    setFile(null)
    setCheckedRows(new Set())
    preview.reset()
    apply.reset()
  }

  const matches = useMemo(() => preview.data?.matches ?? [], [preview.data])
  const matchableRows = matches.filter((match) => match.posting_id !== null && match.proposed_category_id !== null)
  const allChecked = matchableRows.length > 0 && matchableRows.every((match) => checkedRows.has(match.row_number))

  function toggleRow(rowNumber: number, checked: boolean) {
    setCheckedRows((prev) => {
      const next = new Set(prev)
      if (checked) next.add(rowNumber)
      else next.delete(rowNumber)
      return next
    })
  }

  function toggleSelectAll() {
    setCheckedRows(allChecked ? new Set() : new Set(matchableRows.map((match) => match.row_number)))
  }

  function handleApply() {
    if (!file) return
    apply.mutate({ file, confirmedRowNumbers: [...checkedRows] })
  }

  return (
    <div className="space-y-4">
      {!file && <FileDropZone onFile={handleFile} />}

      {file && (
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <span className="text-sm font-medium text-foreground">{file.name}</span>
          <button
            type="button"
            onClick={reset}
            disabled={preview.isPending || apply.isPending}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
            title="Start over with a different file"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}

      {preview.isPending && <LoadingProgressBar step="Matching rows against your ledger…" />}

      {preview.isError && (
        <p className="flex items-start gap-1 text-sm text-destructive">
          <XCircle className="mt-0.5 size-4 shrink-0" />
          {preview.error instanceof Error ? preview.error.message : 'Could not read this file.'}
        </p>
      )}

      {preview.data && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {matchableRows.length} of {matches.length} row{matches.length === 1 ? '' : 's'} matched an existing
            transaction. {matches.length - matchableRows.length > 0 && "Unmatched rows are shown but can't be applied."}
          </p>

          {preview.data.new_categories.length > 0 && (
            <p className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
              This would create {preview.data.new_categories.length} new categor
              {preview.data.new_categories.length === 1 ? 'y' : 'ies'}:{' '}
              {preview.data.new_categories.map((category) => category.name).join(', ')}
            </p>
          )}

          <div className="max-h-[60vh] overflow-x-auto overflow-y-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <input
                      type="checkbox"
                      className="size-3.5 accent-current"
                      checked={allChecked}
                      onChange={toggleSelectAll}
                      aria-label="Select all matched rows"
                    />
                  </TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>File description</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Category to apply</TableHead>
                  <TableHead>Matched transaction</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {matches.map((match) => {
                  const isMatched = match.posting_id !== null && match.proposed_category_id !== null
                  return (
                    <TableRow key={match.row_number} className={isMatched ? undefined : 'opacity-50'}>
                      <TableCell>
                        <input
                          type="checkbox"
                          className="size-3.5 accent-current"
                          checked={checkedRows.has(match.row_number)}
                          disabled={!isMatched}
                          onChange={(event) => toggleRow(match.row_number, event.target.checked)}
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDate(match.posted_at.slice(0, 10))}
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-muted-foreground" title={match.description}>
                        {match.description}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatCurrency(match.amount, 'USD')}</TableCell>
                      <TableCell>{matchLabel(match)}</TableCell>
                      <TableCell
                        className="max-w-[220px] truncate text-muted-foreground"
                        title={match.matched_description ?? undefined}
                      >
                        {match.matched_description ?? "Couldn't find this transaction"}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-end gap-3">
            {apply.isSuccess && (
              <span className="flex items-center gap-1 text-sm text-emerald-600">
                <CheckCircle2 className="size-4" /> Updated {apply.data.updated_posting_count} transaction
                {apply.data.updated_posting_count === 1 ? '' : 's'}
              </span>
            )}
            {apply.isError && (
              <span className="flex items-center gap-1 text-sm text-destructive">
                <XCircle className="size-4" />
                {apply.error instanceof Error ? apply.error.message : 'Could not apply these categories.'}
              </span>
            )}
            <Button onClick={handleApply} disabled={checkedRows.size === 0 || apply.isPending}>
              {apply.isPending
                ? 'Applying…'
                : `Apply categories to ${checkedRows.size} transaction${checkedRows.size === 1 ? '' : 's'}`}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
