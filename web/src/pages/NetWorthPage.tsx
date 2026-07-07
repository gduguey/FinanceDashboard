import { useState, type ReactNode } from 'react'
import { Trash2, TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { NoAccountsYetBanner } from '@/components/shared/NoAccountsYetBanner'
import { PageHeader, type PageHeaderSection } from '@/components/layout/PageHeader'
import { AccountCompositionBar } from '@/components/accounting/AccountCompositionBar'
import { NetWorthHistoryChart } from '@/components/accounting/NetWorthHistoryChart'
import { NetWorthAllocationPie } from '@/components/accounting/NetWorthAllocationPie'
import { InterestTrackingPanel } from '@/components/accounting/InterestTrackingPanel'
import { ExchangeRatePanel } from '@/components/accounting/ExchangeRatePanel'
import { ACCOUNT_KIND_LABELS } from '@/lib/accountKinds'
import { formatCurrency, signColor } from '@/lib/format'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useCurrencies, useNetWorth, useRatesToBase, useSetOtherAssets } from '@/hooks/useAccountingData'
import type { AccountKind, CurrencyCode, NetWorthAccountRow, OtherAsset } from '@/types/accounting'

function StatCard({
  label,
  value,
  colorClass,
  subline,
}: {
  label: string
  value: string
  colorClass?: string
  subline?: ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold tracking-tight tabular-nums ${colorClass ?? 'text-foreground'}`}>
          {value}
        </div>
        {subline}
      </CardContent>
    </Card>
  )
}

// The past 30 days' move — up or down, in both the display currency and
// as a percent of what net worth was 30 days ago — so the headline figure
// always comes with "...and here's where that's trending" right below it.
function NetWorthChangeSubline({
  current,
  past,
  displayCurrency,
}: {
  current: number
  past: number
  displayCurrency: CurrencyCode
}) {
  const delta = current - past
  const pct = past !== 0 ? (delta / Math.abs(past)) * 100 : 0
  const isUp = delta >= 0
  const Icon = isUp ? TrendingUp : TrendingDown
  return (
    <div className={`mt-1 flex items-center gap-1 text-xs ${isUp ? 'text-emerald-600' : 'text-destructive'}`}>
      <Icon className="size-3.5" />
      {formatCurrency(Math.abs(delta), displayCurrency)} ({isUp ? '+' : '-'}
      {Math.abs(pct).toFixed(1)}%) past 30 days
    </div>
  )
}

interface DisplayRow {
  key: string
  name: string
  kind: AccountKind
  currency: CurrencyCode
  balance: number
  parentAccountId: string | null
  otherAssetId: string | null
}

// Vaults are their own `Account` rows (see ACCOUNTING_PLAN.md) rather than a
// soft overlay on their parent's balance, so they need an explicit
// parent-then-children sort here to render nested instead of alphabetized
// away from the account they belong to. Other assets never have a parent,
// so they always fall out at the top level alongside real accounts.
function orderedRows(rows: DisplayRow[]): { row: DisplayRow; depth: number }[] {
  const byParent = new Map<string | null, DisplayRow[]>()
  for (const row of rows) {
    const key = row.parentAccountId
    byParent.set(key, [...(byParent.get(key) ?? []), row])
  }
  const result: { row: DisplayRow; depth: number }[] = []
  function visit(parentId: string | null, depth: number) {
    for (const row of byParent.get(parentId) ?? []) {
      result.push({ row, depth })
      visit(row.key, depth + 1)
    }
  }
  visit(null, 0)
  return result
}

function AccountsTable({
  accounts,
  otherAssets,
  onRemoveOtherAsset,
}: {
  accounts: NetWorthAccountRow[]
  otherAssets: OtherAsset[]
  onRemoveOtherAsset: (assetId: string) => void
}) {
  const rows: DisplayRow[] = [
    ...accounts.map((row) => ({
      key: row.account_id,
      name: row.name,
      kind: row.kind,
      currency: row.currency,
      balance: row.balance,
      parentAccountId: row.parent_account_id,
      otherAssetId: null,
    })),
    ...otherAssets.map((asset) => ({
      key: `other-asset:${asset.asset_id}`,
      name: asset.name,
      kind: 'other_asset' as const,
      currency: asset.currency,
      balance: asset.value,
      parentAccountId: null,
      otherAssetId: asset.asset_id,
    })),
  ]
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'balance')
  const displayRows = sort.key === 'balance' && sort.desc ? orderedRows(rows) : sorted.map((row) => ({ row, depth: 0 }))

  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No accounts yet — import a statement to start.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortableTableHead active={sort.key === 'name'} desc={sort.desc} onClick={() => toggleSort('name')}>
            Account
          </SortableTableHead>
          <SortableTableHead active={sort.key === 'kind'} desc={sort.desc} onClick={() => toggleSort('kind')}>
            Kind
          </SortableTableHead>
          <SortableTableHead active={sort.key === 'currency'} desc={sort.desc} onClick={() => toggleSort('currency')}>
            Currency
          </SortableTableHead>
          <SortableTableHead align="right" active={sort.key === 'balance'} desc={sort.desc} onClick={() => toggleSort('balance')}>
            Balance
          </SortableTableHead>
          <TableHead className="w-8" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {displayRows.map(({ row, depth }) => (
          <TableRow key={row.key}>
            <TableCell style={{ paddingLeft: `${depth * 20 + 16}px` }} className="font-medium">
              {row.name}
            </TableCell>
            <TableCell className="text-muted-foreground">{ACCOUNT_KIND_LABELS[row.kind]}</TableCell>
            <TableCell className="text-muted-foreground">{row.currency}</TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(row.balance)}`}>
              {formatCurrency(row.balance, row.currency)}
            </TableCell>
            <TableCell>
              {row.otherAssetId && (
                <Button variant="ghost" size="icon" onClick={() => onRemoveOtherAsset(row.otherAssetId!)}>
                  <Trash2 className="size-3.5 text-muted-foreground" />
                </Button>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function AddOtherAssetForm({ otherAssets }: { otherAssets: OtherAsset[] }) {
  const setOtherAssets = useSetOtherAssets()
  const [draft, setDraft] = useState<{ name: string; value: string; currency: CurrencyCode; note: string }>({
    name: '',
    value: '',
    currency: 'USD',
    note: '',
  })

  function addAsset() {
    if (!draft.name || !draft.value) return
    const asset: OtherAsset = {
      asset_id: `manual:${draft.name.toLowerCase().replace(/\s+/g, '-')}-${Date.now()}`,
      name: draft.name,
      value: Number(draft.value),
      currency: draft.currency,
      note: draft.note,
    }
    setOtherAssets.mutate([...otherAssets, asset])
    setDraft({ name: '', value: '', currency: draft.currency, note: '' })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add another asset</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Name
            <Input
              className="w-40"
              value={draft.name}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
              placeholder="e.g. Car"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Value
            <Input
              className="w-28"
              type="number"
              value={draft.value}
              onChange={(event) => setDraft((prev) => ({ ...prev, value: event.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Currency
            <Select value={draft.currency} onValueChange={(value) => value && setDraft((prev) => ({ ...prev, currency: value as CurrencyCode }))}>
              <SelectTrigger size="sm" className="w-20">
                <SelectValue items={{ USD: 'USD', EUR: 'EUR' }} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="USD">USD</SelectItem>
                <SelectItem value="EUR">EUR</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Note
            <Input
              className="w-48"
              value={draft.note}
              onChange={(event) => setDraft((prev) => ({ ...prev, note: event.target.value }))}
            />
          </label>
          <Button size="sm" onClick={addAsset}>
            Add
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

// The Guide's own anchor-nav pattern — one long scroll broken into
// thematic, deep-linkable sections rather than tabs, since every block
// here is part of the same "net worth as of now" view.
const SECTIONS: PageHeaderSection[] = [
  { id: 'summary', label: 'Summary' },
  { id: 'history', label: 'History' },
  { id: 'allocation', label: 'Allocation' },
  { id: 'accounts', label: 'Accounts' },
  { id: 'other-assets', label: 'Other assets' },
  { id: 'interest', label: 'Interest' },
  { id: 'exchange-rates', label: 'Exchange rates' },
]

function daysAgoIsoDate(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return date.toISOString().slice(0, 10)
}

export function NetWorthPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data, isLoading } = useNetWorth(undefined, displayCurrency)
  const { data: past } = useNetWorth(daysAgoIsoDate(30), displayCurrency)
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD')
  const ratesToBase = useRatesToBase(nonBaseCurrencies)
  const setOtherAssets = useSetOtherAssets()

  function removeOtherAsset(assetId: string) {
    if (!data) return
    setOtherAssets.mutate(data.other_assets.filter((asset) => asset.asset_id !== assetId))
  }

  // Jumping to History/Allocation/Interest/etc. is meaningless when there's
  // nothing in any of them yet — the anchor nav only earns its place once
  // there's an account for those sections to actually show something about.
  const hasData = Boolean(data) && hasAnyRealAccount(data?.accounts ?? [])

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Net Worth"
        actions={
          <>
            <DisplayCurrencyToggle />
            <ExchangeRateSyncButton />
          </>
        }
        sections={hasData ? SECTIONS : undefined}
      />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            {!hasAnyRealAccount(data.accounts) && <NoAccountsYetBanner />}
            <section id="summary" className="scroll-section grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatCard
                label="Net worth"
                value={formatCurrency(data.net_worth, displayCurrency)}
                colorClass={signColor(data.net_worth)}
                subline={
                  past && (
                    <NetWorthChangeSubline current={data.net_worth} past={past.net_worth} displayCurrency={displayCurrency} />
                  )
                }
              />
              <StatCard label="Assets" value={formatCurrency(data.assets, displayCurrency)} />
              <StatCard
                label="Liabilities"
                value={formatCurrency(-data.liabilities, displayCurrency)}
                colorClass={signColor(-data.liabilities)}
              />
              <StatCard label="Other assets" value={formatCurrency(data.other_assets_total, displayCurrency)} />
            </section>

            <section id="history" className="scroll-section">
              <NetWorthHistoryChart displayCurrency={displayCurrency} />
            </section>

            <section id="allocation" className="scroll-section">
              <NetWorthAllocationPie
                accounts={data.accounts}
                otherAssets={data.other_assets}
                displayCurrency={displayCurrency}
                ratesToBase={ratesToBase}
              />
            </section>

            <section id="accounts" className="scroll-section">
              <Card>
                <CardHeader>
                  <CardTitle>Accounts</CardTitle>
                </CardHeader>
                <CardContent>
                  <AccountCompositionBar
                    accounts={data.accounts}
                    otherAssets={data.other_assets}
                    displayCurrency={displayCurrency}
                    ratesToBase={ratesToBase}
                  />
                  <AccountsTable accounts={data.accounts} otherAssets={data.other_assets} onRemoveOtherAsset={removeOtherAsset} />
                </CardContent>
              </Card>
            </section>

            <section id="other-assets" className="scroll-section">
              <AddOtherAssetForm otherAssets={data.other_assets} />
            </section>

            <section id="interest" className="scroll-section">
              <InterestTrackingPanel />
            </section>

            <section id="exchange-rates" className="scroll-section">
              <ExchangeRatePanel />
            </section>
          </>
        )}
      </div>
    </div>
  )
}
