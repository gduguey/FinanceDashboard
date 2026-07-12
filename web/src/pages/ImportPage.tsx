import { useCallback, useState } from 'react'
import { CheckCircle2, Upload, X, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AccountsManagementTable } from '@/components/accounting/AccountsManagementTable'
import { PaystubReconciliationCard } from '@/components/accounting/PaystubReconciliationCard'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { accountingApi } from '@/lib/accountingApi'
import { useAccountingStore, useImportCsv, usePostings, useRebuildLedger } from '@/hooks/useAccountingData'
import type { Account, CurrencyCode } from '@/types/accounting'

const MAX_FILES_PER_DROP = 8

interface PendingCsvImport {
  key: string
  kind: 'csv'
  file: File
  institution: string
  accountKind: string
  accountId: string
  name: string
  currency: CurrencyCode
  parentAccountId: string | null
  isNewAccount: boolean
  status: 'pending' | 'importing' | 'done' | 'error'
  message?: string
}

const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

type PendingImport = PendingCsvImport

function readFirstLines(file: File, count: number): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? '').split(/\r?\n/).slice(0, count))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file.slice(0, 4096))
  })
}

// Drag-and-drop peeks only at each CSV's header row to guess the bank/account
// (see accounting.importers.detect) — the full file is only read once the
// user confirms, so a bulk drop of several files stays instant.
export function ImportPage() {
  const [pending, setPending] = useState<PendingImport[]>([])
  const { data: store } = useAccountingStore()
  const { data: postings } = usePostings()
  const importCsv = useImportCsv()
  const rebuild = useRebuildLedger()

  const handleFiles = useCallback(
    async (files: FileList) => {
      const accepted = Array.from(files).slice(0, MAX_FILES_PER_DROP)
      const entries = await Promise.all(
        accepted.map(async (file): Promise<PendingImport> => {
          const key = `${file.name}-${file.size}-${Math.random()}`
          const [headerLine, firstDataLine] = await readFirstLines(file, 2)
          const header = (headerLine ?? '').split(',').map((column) => column.trim())
          const firstDataRow = firstDataLine
            ? Object.fromEntries(header.map((column, index) => [column, (firstDataLine.split(',')[index] ?? '').trim()]))
            : undefined
          const detected = await accountingApi.detect(header, file.name, firstDataRow).catch(() => null)
          // A detected account may or may not be registered yet — a
          // brand-new vault CSV, for instance, describes an account
          // nobody's created here before. Either way, the detected
          // guess (not just an already-registered match) is what
          // pre-fills the form, since that's the whole point of detecting.
          const existingAccount = detected ? store?.accounts[detected.account_id] : undefined
          return {
            key,
            kind: 'csv',
            file,
            institution: existingAccount?.institution ?? detected?.institution ?? '',
            accountKind: existingAccount?.kind ?? detected?.account_kind ?? '',
            accountId: existingAccount?.account_id ?? detected?.account_id ?? '',
            name: existingAccount?.name ?? detected?.account_name ?? '',
            currency: existingAccount?.currency ?? 'USD',
            parentAccountId: existingAccount?.parent_account_id ?? detected?.parent_account_id ?? null,
            isNewAccount: Boolean(detected) && !existingAccount,
            status: 'pending',
          }
        }),
      )
      setPending((prev) => [...prev, ...entries])
    },
    [store],
  )

  function updateEntry(key: string, patch: Partial<PendingImport>) {
    setPending((prev) => prev.map((entry) => (entry.key === key ? ({ ...entry, ...patch } as PendingImport) : entry)))
  }

  function dismissEntry(key: string) {
    setPending((prev) => prev.filter((entry) => entry.key !== key))
  }

  async function confirmCsvImport(entry: PendingCsvImport) {
    updateEntry(entry.key, { status: 'importing' })
    try {
      const result = await importCsv.mutateAsync({
        file: entry.file,
        info: {
          institution: entry.institution,
          account_kind: entry.accountKind,
          account_id: entry.accountId,
          account_name: entry.name,
          currency: entry.currency,
          parent_account_id: entry.parentAccountId,
        },
      })
      updateEntry(entry.key, { status: 'done', message: `${result.new_posting_count} new postings` })
    } catch (error) {
      updateEntry(entry.key, { status: 'error', message: error instanceof Error ? error.message : 'Import failed' })
    }
  }

  const registeredAccounts = store ? Object.values(store.accounts).filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id)) : []
  const institutions = [
    ...new Set([
      ...registeredAccounts.map((account) => account.institution),
      ...pending.filter((entry): entry is PendingCsvImport => entry.kind === 'csv' && Boolean(entry.institution)).map((entry) => entry.institution),
    ]),
  ].sort()
  function accountsForInstitution(institution: string): Account[] {
    return registeredAccounts.filter((account) => account.institution === institution).sort((a, b) => a.name.localeCompare(b.name))
  }
  const accountIdsWithPostings = new Set((postings ?? []).map((posting) => posting.account_id))

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
          <div key={entry.key} className="relative flex flex-wrap items-end gap-3 rounded-lg border border-border p-4">
            <button
              type="button"
              onClick={() => dismissEntry(entry.key)}
              disabled={entry.status === 'importing'}
              className="absolute top-2 right-2 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
              title="Abandon this import"
            >
              <X className="size-3.5" />
            </button>
            <div className="min-w-0 flex-1 basis-full text-sm font-medium text-foreground">{entry.file.name}</div>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Institution
              <Select
                value={entry.institution}
                onValueChange={(institution) =>
                  institution &&
                  updateEntry(entry.key, { institution, accountId: '', accountKind: '', name: '', isNewAccount: false })
                }
              >
                <SelectTrigger size="sm" className="w-36">
                  <SelectValue items={Object.fromEntries(institutions.map((i) => [i, i]))} />
                </SelectTrigger>
                <SelectContent>
                  {institutions.map((institution) => (
                    <SelectItem key={institution} value={institution}>
                      {institution}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Account name
              <Select
                value={entry.accountId}
                onValueChange={(accountId) => {
                  const account = accountId ? store?.accounts[accountId] : undefined
                  if (account) {
                    updateEntry(entry.key, {
                      accountId: account.account_id,
                      accountKind: account.kind,
                      name: account.name,
                      currency: account.currency,
                      parentAccountId: account.parent_account_id,
                      isNewAccount: false,
                    })
                  }
                }}
                disabled={!entry.institution}
              >
                <SelectTrigger size="sm" className="w-52">
                  <SelectValue
                    placeholder="Choose an account…"
                    items={{
                      ...Object.fromEntries(accountsForInstitution(entry.institution).map((a) => [a.account_id, a.name])),
                      ...(entry.isNewAccount ? { [entry.accountId]: `${entry.name} (new)` } : {}),
                    }}
                  />
                </SelectTrigger>
                <SelectContent>
                  {entry.isNewAccount && (
                    <SelectItem value={entry.accountId}>{entry.name} (new)</SelectItem>
                  )}
                  {accountsForInstitution(entry.institution).map((account) => (
                    <SelectItem key={account.account_id} value={account.account_id}>
                      {account.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
              onClick={() => confirmCsvImport(entry)}
            >
              Import
            </Button>
            {entry.status === 'importing' && <LoadingProgressBar step="Standardizing rows and merging into the ledger…" />}

            {entry.isNewAccount && (
              <p className="basis-full text-xs text-muted-foreground">
                This account isn't registered yet — importing will create "{entry.name}" automatically.
              </p>
            )}
            {entry.institution && !entry.isNewAccount && accountsForInstitution(entry.institution).length === 0 ? (
              <p className="basis-full text-xs text-amber-600">
                No {entry.institution} accounts registered yet — add one below first, then come back to pick it here.
              </p>
            ) : null}

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

        <div className="flex flex-col items-center gap-2 border-t border-border pt-6">
          <Button variant="outline" size="sm" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
            Rebuild ledger from raw archives
          </Button>
          {rebuild.isPending && <LoadingProgressBar step="Rebuilding ledger from raw archives…" />}
          <p className="max-w-md text-center text-xs text-muted-foreground">
            Recomputes every posting from every archived raw statement — use this to recover if the derived ledger is
            ever wrong or corrupted; nothing you've imported is ever lost.
          </p>
        </div>

        <PaystubReconciliationCard />

        {store && (
          <AccountsManagementTable accounts={store.accounts} accountIdsWithPostings={accountIdsWithPostings} />
        )}
      </div>
    </div>
  )
}
