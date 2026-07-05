import { useCallback, useState } from 'react'
import { CheckCircle2, Upload, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { accountingApi } from '@/lib/accountingApi'
import { useImportCsv, useRebuildLedger } from '@/hooks/useAccountingData'
import type { AccountKind } from '@/types/accounting'

const MAX_FILES_PER_DROP = 8

const ACCOUNT_KINDS_BY_INSTITUTION: Record<string, AccountKind[]> = {
  Chase: ['checking', 'credit_card'],
  SoFi: ['checking', 'savings'],
}
const ALL_ACCOUNT_KINDS: AccountKind[] = ['checking', 'savings', 'credit_card', 'cash', 'loan']

interface PendingImport {
  key: string
  file: File
  institution: string
  accountKind: string
  accountId: string
  accountName: string
  currency: string
  status: 'pending' | 'importing' | 'done' | 'error'
  message?: string
}

function readFirstLine(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? '').split(/\r?\n/, 1)[0] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file.slice(0, 4096))
  })
}

// Drag-and-drop peeks only at each file's header row to guess the bank/account
// (see accounting.importers.detect) — the full file is only read once the
// user confirms, so a bulk drop of several CSVs stays instant.
export function ImportPage() {
  const [pending, setPending] = useState<PendingImport[]>([])
  const importCsv = useImportCsv()
  const rebuild = useRebuildLedger()

  const handleFiles = useCallback(async (files: FileList) => {
    const accepted = Array.from(files).slice(0, MAX_FILES_PER_DROP)
    const entries = await Promise.all(
      accepted.map(async (file) => {
        const firstLine = await readFirstLine(file)
        const header = firstLine.split(',').map((column) => column.trim())
        const detected = await accountingApi.detect(header, file.name).catch(() => null)
        return {
          key: `${file.name}-${file.size}-${Math.random()}`,
          file,
          institution: detected?.institution ?? '',
          accountKind: detected?.account_kind ?? '',
          accountId: detected?.account_id ?? '',
          accountName: detected?.account_name ?? '',
          currency: 'USD',
          status: 'pending' as const,
        }
      }),
    )
    setPending((prev) => [...prev, ...entries])
  }, [])

  function updateEntry(key: string, patch: Partial<PendingImport>) {
    setPending((prev) => prev.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)))
  }

  async function confirmImport(entry: PendingImport) {
    updateEntry(entry.key, { status: 'importing' })
    try {
      const result = await importCsv.mutateAsync({
        file: entry.file,
        info: {
          institution: entry.institution,
          account_kind: entry.accountKind,
          account_id: entry.accountId,
          account_name: entry.accountName,
          currency: entry.currency,
        },
      })
      updateEntry(entry.key, { status: 'done', message: `${result.new_posting_count} new postings` })
    } catch (error) {
      updateEntry(entry.key, { status: 'error', message: error instanceof Error ? error.message : 'Import failed' })
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Import</h1>
      </div>

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        <div
          className="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-6 py-14 text-center text-sm text-muted-foreground"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            if (event.dataTransfer.files.length) void handleFiles(event.dataTransfer.files)
          }}
        >
          <Upload className="size-6 text-muted-foreground/60" />
          Drag and drop up to {MAX_FILES_PER_DROP} bank CSV exports here
          <label className="cursor-pointer text-primary underline-offset-4 hover:underline">
            or browse
            <input
              type="file"
              accept=".csv,.CSV"
              multiple
              className="hidden"
              onChange={(event) => {
                if (event.target.files?.length) void handleFiles(event.target.files)
                event.target.value = ''
              }}
            />
          </label>
        </div>

        {pending.map((entry) => (
          <div key={entry.key} className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-4">
            <div className="min-w-0 flex-1 basis-full text-sm font-medium text-foreground">{entry.file.name}</div>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Institution
              <Input
                className="w-32"
                value={entry.institution}
                onChange={(event) => updateEntry(entry.key, { institution: event.target.value })}
              />
            </label>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Account kind
              <Select
                value={entry.accountKind}
                onValueChange={(value) => value && updateEntry(entry.key, { accountKind: value })}
              >
                <SelectTrigger size="sm" className="w-40">
                  <SelectValue placeholder="Choose…" />
                </SelectTrigger>
                <SelectContent>
                  {(ACCOUNT_KINDS_BY_INSTITUTION[entry.institution] ?? ALL_ACCOUNT_KINDS).map((kind) => (
                    <SelectItem key={kind} value={kind}>
                      {kind}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Account id
              <Input
                className="w-44"
                value={entry.accountId}
                onChange={(event) => updateEntry(entry.key, { accountId: event.target.value })}
              />
            </label>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Display name
              <Input
                className="w-52"
                value={entry.accountName}
                onChange={(event) => updateEntry(entry.key, { accountName: event.target.value })}
              />
            </label>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Currency
              <Input
                className="w-20"
                value={entry.currency}
                onChange={(event) => updateEntry(entry.key, { currency: event.target.value })}
              />
            </label>

            <Button
              size="sm"
              disabled={
                entry.status === 'importing' ||
                entry.status === 'done' ||
                !entry.institution ||
                !entry.accountKind ||
                !entry.accountId
              }
              onClick={() => confirmImport(entry)}
            >
              {entry.status === 'importing' ? 'Importing…' : 'Import'}
            </Button>

            {entry.status === 'done' && (
              <span className="flex items-center gap-1 text-xs text-emerald-600">
                <CheckCircle2 className="size-3.5" /> {entry.message}
              </span>
            )}
            {entry.status === 'error' && (
              <span className="flex items-center gap-1 text-xs text-destructive">
                <XCircle className="size-3.5" /> {entry.message}
              </span>
            )}
          </div>
        ))}

        <div className="border-t border-border pt-4">
          <Button variant="outline" size="sm" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
            {rebuild.isPending ? 'Rebuilding…' : 'Rebuild ledger from raw archives'}
          </Button>
          <p className="mt-1 text-xs text-muted-foreground">
            Recomputes every posting from every archived raw CSV — use this to recover if the derived ledger is
            ever wrong or corrupted; nothing you've imported is ever lost.
          </p>
        </div>
      </div>
    </div>
  )
}
