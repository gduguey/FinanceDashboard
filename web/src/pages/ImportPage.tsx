import { ArrowRight, CheckCircle2, Info, Landmark, Upload, X, XCircle } from 'lucide-react'
import { type ReactNode, useCallback, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { TaxonomyTable } from '@/components/accounting/CategoriesTab'
import { CategorizeFromFilePanel } from '@/components/accounting/CategorizeFromFilePanel'
import { DuplicateSuggestionsPanel } from '@/components/accounting/DuplicateSuggestionsPanel'
import { PaystubReconciliationCard } from '@/components/accounting/PaystubReconciliationCard'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useAccountingStore,
  useCanonicalImportPreview,
  useImportCanonicalCsv,
  useImportCsv,
  useRebuildLedger,
  useSupportedImportKinds,
} from '@/hooks/useAccountingData'
import { accountingApi, type ImportAccountInfo } from '@/lib/accountingApi'
import { ACCOUNT_KIND_LABELS } from '@/lib/accountKinds'
import type { Account, CanonicalCategoryOverrides, Category, CurrencyCode } from '@/types/accounting'

const MAX_FILES_PER_DROP = 8

// Each label spells out the actual character, not just its name — "Comma"
// alone doesn't tell you whether the file needs `,` or something else if
// you're not sure what a "semicolon" looks like at a glance.
const SEPARATOR_ITEMS: Record<string, string> = {
  ',': 'Comma (,)',
  ';': 'Semicolon (;)',
  '\t': 'Tab',
  '|': 'Pipe (|)',
}
// The long descriptive text belongs in the dropdown's own options list,
// where the popup can wrap it — it doesn't fit in the closed trigger box,
// which needs its own short label per value instead (see `SelectValue`'s
// `items` prop, which only ever governs what shows once collapsed).
const DATE_ORDER_ITEMS: Record<string, string> = {
  MDY: 'Month/Day (US, e.g. 01/12 = Jan 12)',
  DMY: 'Day/Month (e.g. 01/12 = 12 Jan)',
}
const DATE_ORDER_TRIGGER_LABELS: Record<string, string> = {
  MDY: 'Month/Day (US)',
  DMY: 'Day/Month',
}

interface PendingCsvImport {
  key: string
  kind: 'csv'
  file: File
  institution: string
  accountKind: string
  accountId: string
  currency: CurrencyCode
  // Non-empty only when detection matched 2+ already-registered accounts of
  // the same institution+kind — the ambiguous case, where the account
  // `<Select>` below is shown but nothing is auto-picked for the user.
  candidateAccountIds: string[]
  status: 'pending' | 'importing' | 'done' | 'error'
  message?: string
  // Only meaningful once `institution`/`accountKind` have no registered
  // standardizer (see `useSupportedImportKinds`) — the canonical fallback
  // parser guesses the column separator, but a first guess can fail on an
  // unusual file, so a retry can pin one down explicitly.
  separator?: string
  // Only ambiguous, all-numeric dates (01/12) are affected — an
  // unambiguous format (2026-06-30, Jun 30 2026) parses the same
  // regardless, so this is left unset by default rather than forcing a
  // choice; pin it down only for a file known to export ambiguous
  // day-first dates, since a wrong guess there doesn't fail to parse, it
  // just silently reads the wrong date.
  dateOrder?: 'MDY' | 'DMY'
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
  const topLevelIds = new Set(
    newCategories.filter((category) => !category.parent_category_id).map((c) => c.category_id),
  )
  const subcategoryNamesByParentId = new Map<string, string[]>()
  for (const category of newCategories) {
    if (!category.parent_category_id) continue
    const names = subcategoryNamesByParentId.get(category.parent_category_id) ?? []
    names.push(category.name)
    subcategoryNamesByParentId.set(category.parent_category_id, names)
  }
  const parentIds = new Set([...topLevelIds, ...subcategoryNamesByParentId.keys()])
  const parents = [...parentIds]
    .map((id) => allCategories[id])
    .filter((category): category is Category => Boolean(category))

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
      <Link to="/categories" className="inline-block text-xs text-primary underline-offset-4 hover:underline">
        Review or edit these in Category taxonomy →
      </Link>
    </div>
  )
}

// Preview-only categories (never persisted yet) grouped the same way as
// `groupNewCategories`, but keeping the `Category` objects themselves
// (rather than just names) since each row needs its own editable name.
function groupPreviewCategories(categories: Category[]): {
  expense: { category: Category; children: Category[] }[]
  income: { category: Category; children: Category[] }[]
} {
  const childrenByParentId = new Map<string, Category[]>()
  for (const category of categories) {
    if (!category.parent_category_id) continue
    const siblings = childrenByParentId.get(category.parent_category_id) ?? []
    siblings.push(category)
    childrenByParentId.set(category.parent_category_id, siblings)
  }
  const topLevel = categories.filter((category) => !category.parent_category_id)
  const expense = topLevel
    .filter((category) => category.classification === 'expense')
    .map((category) => ({ category, children: childrenByParentId.get(category.category_id) ?? [] }))
  const income = topLevel
    .filter((category) => category.classification === 'income')
    .map((category) => ({ category, children: childrenByParentId.get(category.category_id) ?? [] }))
  return { expense, income }
}

// Shown before a canonical import actually creates anything, so the user can
// rename categories/subcategories freely first — renaming two entries to the
// same name merges them (see `accounting.importers.canonical.csv.CategoryOverrides`).
function ValidateCategoriesDialog({
  categories,
  isSubmitting,
  onCancel,
  onConfirm,
}: {
  categories: Category[]
  isSubmitting: boolean
  onCancel: () => void
  onConfirm: (overrides: CanonicalCategoryOverrides) => void
}) {
  const [names, setNames] = useState<Record<string, string>>(() =>
    Object.fromEntries(categories.map((category) => [category.category_id, category.name])),
  )
  const { expense, income } = groupPreviewCategories(categories)
  const hasBlankName = categories.some((category) => !names[category.category_id]?.trim())

  function handleConfirm() {
    const overrides: CanonicalCategoryOverrides = { categories: {}, subcategories: {} }
    for (const { category, children } of [...expense, ...income]) {
      const finalName = names[category.category_id].trim()
      if (finalName !== category.name) overrides.categories[category.name] = finalName
      for (const child of children) {
        const finalChildName = names[child.category_id].trim()
        if (finalChildName !== child.name) {
          overrides.subcategories[category.name] = {
            ...overrides.subcategories[category.name],
            [child.name]: finalChildName,
          }
        }
      }
    }
    onConfirm(overrides)
  }

  function renderGroup(title: string, group: { category: Category; children: Category[] }[]) {
    if (group.length === 0) return null
    return (
      <div className="space-y-3">
        <p className="text-xs font-medium text-muted-foreground">{title}</p>
        <div className="space-y-2">
          {group.map(({ category, children }) => (
            <div key={category.category_id} className="space-y-1.5">
              <Input
                value={names[category.category_id]}
                onChange={(event) => setNames((prev) => ({ ...prev, [category.category_id]: event.target.value }))}
                className="h-8 text-sm font-medium"
              />
              {children.map((child) => (
                <Input
                  key={child.category_id}
                  value={names[child.category_id]}
                  onChange={(event) => setNames((prev) => ({ ...prev, [child.category_id]: event.target.value }))}
                  className="ml-5 h-7 w-[calc(100%-1.25rem)] text-xs"
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Review new categories</DialogTitle>
          <DialogDescription>
            This file would create {categories.length} categor{categories.length === 1 ? 'y' : 'ies'}. Rename any of
            them below before importing — renaming two entries to the same name merges them into one.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {renderGroup('Expense', expense)}
          {renderGroup('Income', income)}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={isSubmitting || hasBlankName}>
            {isSubmitting ? 'Importing…' : 'Confirm and import'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Shared visual shell for both tabs' "what this accepts" callouts — same
// muted-card treatment `GuidePage`'s `Definition` uses, so an explanation
// box reads the same whether it's teaching a term or describing a file format.
function InfoBox({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 rounded-lg border border-foreground/10 bg-muted/40 px-4 py-3.5">
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        <p className="text-sm leading-relaxed text-muted-foreground">{children}</p>
      </div>
    </div>
  )
}

const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

type PendingImport = PendingCsvImport

function readFirstLines(file: File, count: number): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () =>
      resolve(
        String(reader.result ?? '')
          .split(/\r?\n/)
          .slice(0, count),
      )
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file.slice(0, 4096))
  })
}

// Drag-and-drop peeks only at each CSV's header row to guess the bank/account
// (see accounting.importers.detect) — the full file is only read once the
// user confirms, so a bulk drop of several files stays instant.
export function ImportPage() {
  const [pending, setPending] = useState<PendingImport[]>([])
  const [categoryValidation, setCategoryValidation] = useState<{
    entry: PendingCsvImport
    categories: Category[]
  } | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'import'
  const { data: store } = useAccountingStore()
  const { data: supportedImportKindsList } = useSupportedImportKinds()
  const importCsv = useImportCsv()
  const importCanonicalCsv = useImportCanonicalCsv()
  const previewCanonicalImport = useCanonicalImportPreview()
  const rebuild = useRebuildLedger()

  const supportedKinds = new Set(
    (supportedImportKindsList ?? []).map((entry) => `${entry.institution}:${entry.account_kind}`),
  )
  const registeredAccounts = store
    ? Object.values(store.accounts).filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    : []

  const handleFiles = useCallback(
    async (files: FileList) => {
      const accepted = Array.from(files).slice(0, MAX_FILES_PER_DROP)
      const entries = await Promise.all(
        accepted.map(async (file): Promise<PendingImport> => {
          const key = `${file.name}-${file.size}-${Math.random()}`
          // An Excel file's bytes aren't text — reading it as one to sniff a
          // header would just produce garbage, and no institution's export
          // is auto-detected in that format anyway, so detection is skipped
          // entirely; the canonical fallback parser (which does understand
          // `.xlsx`) still handles it once the user fills in the form by hand.
          const isExcel = /\.xlsx?$/i.test(file.name)
          const [headerLine, firstDataLine] = isExcel ? [] : await readFirstLines(file, 2)
          const header = (headerLine ?? '').split(',').map((column) => column.trim())
          const firstDataRow = firstDataLine
            ? Object.fromEntries(
                header.map((column, index) => [column, (firstDataLine.split(',')[index] ?? '').trim()]),
              )
            : undefined
          const detected = isExcel
            ? null
            : await accountingApi.detect(header, file.name, firstDataRow).catch(() => null)
          // The detector only ever names an institution and account kind now
          // (see `importers.detect`) — it never invents an account id, so a
          // match against an already-registered account is always by
          // institution+kind, never an exact id lookup. The user can always
          // override the autofill manually via the account `<Select>` below.
          const matches = detected
            ? registeredAccounts.filter(
                (account) =>
                  account.institution.trim().toLowerCase() === detected.institution.trim().toLowerCase() &&
                  account.kind === detected.account_kind,
              )
            : []
          return {
            key,
            kind: 'csv',
            file,
            // Use a matched account's own institution/kind spelling (not the
            // detector's) even in the ambiguous case, so the account picker's
            // exact-match filter actually finds the candidate accounts instead
            // of showing "No accounts registered".
            institution: matches.length > 0 ? matches[0].institution : (detected?.institution ?? ''),
            accountKind: matches.length > 0 ? matches[0].kind : (detected?.account_kind ?? ''),
            accountId: matches.length === 1 ? matches[0].account_id : '',
            currency: matches.length === 1 ? matches[0].currency : 'USD',
            candidateAccountIds: matches.length > 1 ? matches.map((account) => account.account_id) : [],
            status: 'pending',
          }
        }),
      )
      setPending((prev) => [...prev, ...entries])
    },
    [registeredAccounts],
  )

  function updateEntry(key: string, patch: Partial<PendingImport>) {
    setPending((prev) => prev.map((entry) => (entry.key === key ? ({ ...entry, ...patch } as PendingImport) : entry)))
  }

  function dismissEntry(key: string) {
    setPending((prev) => prev.filter((entry) => entry.key !== key))
  }

  function buildImportInfo(entry: PendingCsvImport): ImportAccountInfo {
    // `entry.accountId` is always a real, already-existing account by
    // construction (the `Import` button is disabled while it's empty — see
    // below), so its name/parent are always safe to look up here rather
    // than carried on the pending entry itself.
    const account = store?.accounts[entry.accountId]
    return {
      institution: entry.institution,
      account_kind: entry.accountKind,
      account_id: entry.accountId,
      account_name: account?.name ?? '',
      currency: entry.currency,
      parent_account_id: account?.parent_account_id ?? null,
    }
  }

  async function runCanonicalImport(entry: PendingCsvImport, overrides?: CanonicalCategoryOverrides) {
    updateEntry(entry.key, { status: 'importing' })
    try {
      const result = await importCanonicalCsv.mutateAsync({
        file: entry.file,
        info: buildImportInfo(entry),
        separator: entry.separator,
        dateOrder: entry.dateOrder,
        categoryOverrides: overrides,
      })
      updateEntry(entry.key, {
        status: 'done',
        message: `${result.new_posting_count} new postings`,
        newCategories: result.new_categories,
      })
    } catch (error) {
      updateEntry(entry.key, { status: 'error', message: error instanceof Error ? error.message : 'Import failed' })
    }
    setCategoryValidation(null)
  }

  async function confirmCsvImport(entry: PendingCsvImport) {
    updateEntry(entry.key, { status: 'importing' })
    if (supportedKinds.has(`${entry.institution}:${entry.accountKind}`)) {
      try {
        const result = await importCsv.mutateAsync({ file: entry.file, info: buildImportInfo(entry) })
        updateEntry(entry.key, { status: 'done', message: `${result.new_posting_count} new postings` })
      } catch (error) {
        updateEntry(entry.key, { status: 'error', message: error instanceof Error ? error.message : 'Import failed' })
      }
      return
    }
    // No dedicated standardizer for this institution/kind — fall back to the
    // canonical parser. It's previewed first (never persists anything) so
    // any newly-detected categories can be renamed/merged before creation.
    try {
      const preview = await previewCanonicalImport.mutateAsync({
        file: entry.file,
        accountId: entry.accountId,
        currency: entry.currency,
        separator: entry.separator,
        dateOrder: entry.dateOrder,
      })
      if (preview.new_categories.length > 0) {
        updateEntry(entry.key, { status: 'pending' })
        setCategoryValidation({ entry, categories: preview.new_categories })
        return
      }
      await runCanonicalImport(entry)
    } catch (error) {
      updateEntry(entry.key, { status: 'error', message: error instanceof Error ? error.message : 'Import failed' })
    }
  }

  const institutions = [
    ...new Set([
      ...registeredAccounts.map((account) => account.institution),
      ...pending
        .filter((entry): entry is PendingCsvImport => entry.kind === 'csv' && Boolean(entry.institution))
        .map((entry) => entry.institution),
    ]),
  ].sort()
  function accountsForInstitution(institution: string): Account[] {
    return registeredAccounts
      .filter((account) => account.institution === institution)
      .sort((a, b) => a.name.localeCompare(b.name))
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Import" />

      <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
        <Tabs value={tab} onValueChange={(value) => setSearchParams(value === 'import' ? {} : { tab: value })}>
          <TabsList>
            <TabsTrigger value="import">Import statements</TabsTrigger>
            <TabsTrigger value="categorize">Categorize from file</TabsTrigger>
            <TabsTrigger value="duplicates">Duplicates</TabsTrigger>
            <TabsTrigger value="paystub">Paystub reconciliation</TabsTrigger>
          </TabsList>

          <TabsContent value="import" className="space-y-6">
            <InfoBox title="What files this accepts">
              A CSV or Excel export from any bank works here. Chase and SoFi exports are recognized automatically;
              anything else goes through the generic parser, which needs a{' '}
              <strong className="text-foreground">Date</strong> column, a{' '}
              <strong className="text-foreground">Description</strong> column, and either an{' '}
              <strong className="text-foreground">Amount</strong> column (positive for money in, negative for money out)
              or separate <strong className="text-foreground">Debit</strong>/
              <strong className="text-foreground">Credit</strong> columns — that's the 3 it needs. Two more are
              optional: a <strong className="text-foreground">Category</strong> column and a{' '}
              <strong className="text-foreground">Subcategory</strong> column, which create or match categories
              automatically if present.
            </InfoBox>
            {registeredAccounts.length === 0 && (
              <Link
                to="/accounts"
                className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-3 text-sm transition-colors hover:bg-muted/70"
              >
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Landmark className="size-4" />
                  No accounts yet — a statement needs somewhere to attach to. Create one first, then come back to
                  import.
                </span>
                <span className="flex items-center gap-1 font-medium text-foreground">
                  Create account <ArrowRight className="size-3.5" />
                </span>
              </Link>
            )}
            <div
              className="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-6 py-14 text-center text-sm text-muted-foreground"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (event.dataTransfer.files.length) void handleFiles(event.dataTransfer.files)
              }}
            >
              <Upload className="size-6 text-muted-foreground/60" />
              Drag and drop up to {MAX_FILES_PER_DROP} bank CSV or Excel exports here
              <label className="cursor-pointer text-primary underline-offset-4 hover:underline">
                or browse
                <input
                  type="file"
                  accept=".csv,.CSV,.xlsx,.xls"
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
              <div
                key={entry.key}
                className="relative flex flex-wrap items-end gap-3 rounded-lg border border-border p-4"
              >
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
                      updateEntry(entry.key, {
                        institution,
                        accountId: '',
                        accountKind: '',
                        candidateAccountIds: [],
                      })
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
                          currency: account.currency,
                          candidateAccountIds: [],
                        })
                      }
                    }}
                    disabled={!entry.institution}
                  >
                    <SelectTrigger size="sm" className="w-52">
                      <SelectValue
                        placeholder="Choose an account…"
                        items={Object.fromEntries(
                          accountsForInstitution(entry.institution).map((a) => [a.account_id, a.name]),
                        )}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {accountsForInstitution(entry.institution).map((account) => (
                        <SelectItem key={account.account_id} value={account.account_id}>
                          {account.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>

                {entry.institution &&
                  entry.accountKind &&
                  !supportedKinds.has(`${entry.institution}:${entry.accountKind}`) && (
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                      Date order
                      <Select
                        value={entry.dateOrder ?? ''}
                        onValueChange={(value) =>
                          updateEntry(entry.key, { dateOrder: (value || undefined) as 'MDY' | 'DMY' | undefined })
                        }
                      >
                        <SelectTrigger size="sm" className="w-40">
                          <SelectValue placeholder="Auto (unambiguous)" items={DATE_ORDER_TRIGGER_LABELS} />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.entries(DATE_ORDER_ITEMS).map(([value, label]) => (
                            <SelectItem key={value} value={value}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </label>
                  )}

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
                {entry.status === 'importing' && (
                  <LoadingProgressBar step="Standardizing rows and merging into the ledger…" />
                )}

                {entry.candidateAccountIds.length > 1 && !entry.accountId && (
                  <p className="basis-full text-xs text-muted-foreground">
                    Recognized as a {entry.institution}{' '}
                    {(ACCOUNT_KIND_LABELS as Record<string, string>)[entry.accountKind] ?? entry.accountKind} account —
                    which one?{' '}
                    {entry.candidateAccountIds
                      .map((accountId) => store?.accounts[accountId]?.name)
                      .filter(Boolean)
                      .join(', ')}
                  </p>
                )}
                {entry.institution && !entry.accountId && accountsForInstitution(entry.institution).length === 0 ? (
                  <p className="basis-full text-xs text-amber-600">
                    No {entry.institution} accounts registered yet — add one below first, then come back to pick it
                    here.
                  </p>
                ) : null}
                {entry.institution &&
                  entry.accountKind &&
                  !supportedKinds.has(`${entry.institution}:${entry.accountKind}`) &&
                  entry.status === 'pending' && (
                    <p className="basis-full text-xs text-muted-foreground">
                      No dedicated importer for {entry.institution} — this will go through the generic CSV parser, which
                      looks for Date/Description/Amount (or Debit/Credit) columns and guesses the date and number
                      format.
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
                Recomputes every posting from every archived raw statement — use this to recover if the derived ledger
                is ever wrong or corrupted; nothing you've imported is ever lost.
              </p>
            </div>
          </TabsContent>

          <TabsContent value="categorize" className="space-y-6">
            <InfoBox title="What this is for">
              Same file format as Import statements — a CSV or Excel export with a Date, Description, and Amount (or
              Debit/Credit) column, plus optional Category and Subcategory columns. But this tab doesn't add new
              transactions. It's for when you've already been tracking your own spending by hand — in a spreadsheet
              built from old statements, categorized transaction by transaction — and want to bring that categorization
              work into transactions that are already sitting in your ledger here, instead of starting over from
              scratch.{' '}
              <em>
                For example: your spreadsheet has a row for 7/3/2026, –$5.00, "Starbucks," categorized as Dining Out. If
                that same transaction already exists in your ledger — say, from a Chase statement you imported earlier —
                this sets its category to Dining Out. It never creates a second transaction for it.
              </em>
            </InfoBox>
            <CategorizeFromFilePanel />
          </TabsContent>

          <TabsContent value="duplicates">
            {store && <DuplicateSuggestionsPanel accounts={store.accounts} />}
          </TabsContent>

          <TabsContent value="paystub">
            <PaystubReconciliationCard />
          </TabsContent>
        </Tabs>
      </div>

      {categoryValidation && (
        <ValidateCategoriesDialog
          categories={categoryValidation.categories}
          isSubmitting={importCanonicalCsv.isPending}
          onCancel={() => {
            updateEntry(categoryValidation.entry.key, { status: 'pending' })
            setCategoryValidation(null)
          }}
          onConfirm={(overrides) => void runCanonicalImport(categoryValidation.entry, overrides)}
        />
      )}
    </div>
  )
}
