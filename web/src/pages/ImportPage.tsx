import { useCallback, useState } from 'react'
import { CheckCircle2, Upload, X, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AccountsManagementTable } from '@/components/accounting/AccountsManagementTable'
import { TaxonomyTable } from '@/components/accounting/CategoriesTab'
import { PaystubReconciliationCard } from '@/components/accounting/PaystubReconciliationCard'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { PageHeader } from '@/components/layout/PageHeader'
import { accountingApi } from '@/lib/accountingApi'
import {
  useAccountingStore,
  useImportCanonicalCsv,
  useImportCsv,
  usePostings,
  useRebuildLedger,
  useSupportedImportKinds,
} from '@/hooks/useAccountingData'
import type { Account, Category, CurrencyCode } from '@/types/accounting'

const MAX_FILES_PER_DROP = 8

const SEPARATOR_ITEMS: Record<string, string> = { ',': 'Comma', ';': 'Semicolon', '\t': 'Tab', '|': 'Pipe' }

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
  // Only meaningful once `institution`/`accountKind` have no registered
  // standardizer (see `useSupportedImportKinds`) — the canonical fallback
  // parser guesses the column separator, but a first guess can fail on an
  // unusual file, so a retry can pin one down explicitly.
  separator?: string
  newCategories?: Category[]
}

// Groups a canonical import's newly-created categories/subcategories into
// the same `[name, subcategoryNames[]]` shape `CategoriesTab`'s taxonomy
// reference table already renders — a subcategory whose parent category
// already existed (so isn't itself in `newCategories`) still needs that
// parent's name, hence the `allCategories` lookup.
function groupNewCategories(
  newCategories: Category[],
  allCategories: Record<string, Category>,
): { expense: [string, string[]][]; income: [string, string[]][] } {
  const topLevelIds = new Set(newCategories.filter((category) => !category.parent_category_id).map((c) => c.category_id))
  const subcategoryNamesByParentId = new Map<string, string[]>()
  for (const category of newCategories) {
    if (!category.parent_category_id) continue
    const names = subcategoryNamesByParentId.get(category.parent_category_id) ?? []
    names.push(category.name)
    subcategoryNamesByParentId.set(category.parent_category_id, names)
  }
  const parentIds = new Set([...topLevelIds, ...subcategoryNamesByParentId.keys()])
  const parents = [...parentIds].map((id) => allCategories[id]).filter((category): category is Category => Boolean(category))

  const expense: [string, string[]][] = []
  const income: [string, string[]][] = []
  for (const category of parents) {
    const row: [string, string[]] = [category.name, subcategoryNamesByParentId.get(category.category_id) ?? []]
    ;(category.classification === 'expense' ? expense : income).push(row)
  }
  return { expense, income }
}

function NewCategoriesSummary({
  newCategories,
  allCategories,
}: {
  newCategories: Category[]
  allCategories: Record<string, Category>
}) {
  const { expense, income } = groupNewCategories(newCategories, allCategories)
  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <p className="text-xs text-muted-foreground">
        Created {newCategories.length} categor{newCategories.length === 1 ? 'y' : 'ies'} from this file's
        Category/Subcategory columns:
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {expense.length > 0 && <TaxonomyTable title="Expense" taxonomy={expense} />}
        {income.length > 0 && <TaxonomyTable title="Income" taxonomy={income} />}
      </div>
      <Link to="/accounting?tab=categories" className="inline-block text-xs text-primary underline-offset-4 hover:underline">
        Review or edit these in Category taxonomy →
      </Link>
    </div>
  )
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
  const { data: supportedImportKindsList } = useSupportedImportKinds()
  const importCsv = useImportCsv()
  const importCanonicalCsv = useImportCanonicalCsv()
  const rebuild = useRebuildLedger()

  const supportedKinds = new Set((supportedImportKindsList ?? []).map((entry) => `${entry.institution}:${entry.account_kind}`))

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
    const info = {
      institution: entry.institution,
      account_kind: entry.accountKind,
      account_id: entry.accountId,
      account_name: entry.name,
      currency: entry.currency,
      parent_account_id: entry.parentAccountId,
    }
    try {
      if (supportedKinds.has(`${entry.institution}:${entry.accountKind}`)) {
        const result = await importCsv.mutateAsync({ file: entry.file, info })
        updateEntry(entry.key, { status: 'done', message: `${result.new_posting_count} new postings` })
      } else {
        // No dedicated standardizer for this institution/kind — fall back
        // to the canonical parser, which guesses the file's column names
        // and date/amount formats instead of expecting an exact shape.
        const result = await importCanonicalCsv.mutateAsync({ file: entry.file, info, separator: entry.separator })
        updateEntry(entry.key, {
          status: 'done',
          message: `${result.new_posting_count} new postings`,
          newCategories: result.new_categories,
        })
      }
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
      <PageHeader title="Import" />

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
            {entry.institution &&
              entry.accountKind &&
              !supportedKinds.has(`${entry.institution}:${entry.accountKind}`) &&
              entry.status === 'pending' && (
                <p className="basis-full text-xs text-muted-foreground">
                  No dedicated importer for {entry.institution} — this will go through the generic CSV parser, which
                  looks for Date/Description/Amount (or Debit/Credit) columns and guesses the date and number format.
                </p>
              )}

            {entry.status === 'done' && (
              <div className="basis-full space-y-2">
                <span className="flex items-center gap-1 text-xs text-emerald-600">
                  <CheckCircle2 className="size-3.5" /> {entry.message}
                </span>
                {entry.newCategories && entry.newCategories.length > 0 && store && (
                  <NewCategoriesSummary newCategories={entry.newCategories} allCategories={store.categories} />
                )}
              </div>
            )}
            {entry.status === 'error' && (
              <div className="basis-full space-y-2">
                <span className="flex items-start gap-1 text-xs text-destructive">
                  <XCircle className="mt-0.5 size-3.5 shrink-0" /> {entry.message}
                </span>
                {!supportedKinds.has(`${entry.institution}:${entry.accountKind}`) && (
                  <div className="flex items-end gap-2">
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                      Separator
                      <Select
                        value={entry.separator ?? ''}
                        onValueChange={(value) => updateEntry(entry.key, { separator: value || undefined })}
                      >
                        <SelectTrigger size="sm" className="w-28">
                          <SelectValue placeholder="Auto-detect" items={SEPARATOR_ITEMS} />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.entries(SEPARATOR_ITEMS).map(([value, label]) => (
                            <SelectItem key={value} value={value}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </label>
                    <Button size="sm" variant="outline" onClick={() => confirmCsvImport(entry)}>
                      Retry
                    </Button>
                  </div>
                )}
              </div>
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
