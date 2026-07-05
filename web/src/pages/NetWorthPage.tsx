import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { NetWorthHistoryChart } from '@/components/accounting/NetWorthHistoryChart'
import { NetWorthAllocationPie } from '@/components/accounting/NetWorthAllocationPie'
import { ExchangeRatePanel } from '@/components/accounting/ExchangeRatePanel'
import { formatCurrency, signColor } from '@/lib/format'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { useCurrencies, useNetWorth, useRatesToBase, useSetOtherAssets } from '@/hooks/useAccountingData'
import type { CurrencyCode, NetWorthAccountRow, OtherAsset } from '@/types/accounting'

function StatCard({ label, value, colorClass }: { label: string; value: string; colorClass?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-semibold tracking-tight tabular-nums ${colorClass ?? 'text-foreground'}`}>
          {value}
        </div>
      </CardContent>
    </Card>
  )
}

interface DisplayRow {
  key: string
  name: string
  kind: string
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
      kind: 'other_asset',
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
            <TableCell className="text-muted-foreground">{row.kind === 'other_asset' ? 'other asset' : row.kind}</TableCell>
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

export function NetWorthPage() {
  const { displayCurrency } = useDisplayCurrency()
  const { data, isLoading } = useNetWorth(undefined, displayCurrency)
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).map((currency) => currency.code).filter((code) => code !== 'USD')
  const ratesToBase = useRatesToBase(nonBaseCurrencies)
  const setOtherAssets = useSetOtherAssets()

  function removeOtherAsset(assetId: string) {
    if (!data) return
    setOtherAssets.mutate(data.other_assets.filter((asset) => asset.asset_id !== assetId))
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Net Worth</h1>
        <DisplayCurrencyToggle />
      </div>

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatCard
                label="Net worth"
                value={formatCurrency(data.net_worth, displayCurrency)}
                colorClass={signColor(data.net_worth)}
              />
              <StatCard label="Assets" value={formatCurrency(data.assets, displayCurrency)} />
              <StatCard
                label="Liabilities"
                value={formatCurrency(-data.liabilities, displayCurrency)}
                colorClass={signColor(-data.liabilities)}
              />
              <StatCard label="Other assets" value={formatCurrency(data.other_assets_total, displayCurrency)} />
            </div>

            <NetWorthHistoryChart displayCurrency={displayCurrency} />
            <NetWorthAllocationPie
              accounts={data.accounts}
              otherAssets={data.other_assets}
              displayCurrency={displayCurrency}
              ratesToBase={ratesToBase}
            />

            <Card>
              <CardHeader>
                <CardTitle>Accounts</CardTitle>
              </CardHeader>
              <CardContent>
                <AccountsTable accounts={data.accounts} otherAssets={data.other_assets} onRemoveOtherAsset={removeOtherAsset} />
              </CardContent>
            </Card>

            <AddOtherAssetForm otherAssets={data.other_assets} />

            <ExchangeRatePanel />
          </>
        )}
      </div>
    </div>
  )
}
