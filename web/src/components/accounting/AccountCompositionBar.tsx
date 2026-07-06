import { accountKindGroup, type AccountKindGroup } from '@/lib/accountKinds'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency } from '@/lib/format'
import type { CurrencyCode, NetWorthAccountRow, OtherAsset } from '@/types/accounting'

const GROUP_COLORS: Record<AccountKindGroup, string> = {
  Cash: '#2563eb',
  Savings: '#059669',
  Investment: '#7c3aed',
  'Other assets': '#d97706',
  Loan: '#dc2626',
}
// Fixed left-to-right order regardless of size, so the bar reads the same
// way every time rather than reshuffling as balances change day to day.
const GROUP_ORDER: AccountKindGroup[] = ['Cash', 'Savings', 'Investment', 'Other assets', 'Loan']

// A single stacked bar summarizing every account's balance by its coarse
// kind group (see `lib/accountKinds`) — `income_source`/`expense_payee`
// (the virtual placeholders every posting starts pointed at) never
// appear here, they aren't real money. A loan balance is usually
// negative (money owed); its segment width uses the group's absolute
// total so the bar always sums to a full, readable bar, while the
// legend and tooltip still show the real signed amount.
export function AccountCompositionBar({
  accounts,
  otherAssets,
  displayCurrency,
  ratesToBase,
}: {
  accounts: NetWorthAccountRow[]
  otherAssets: OtherAsset[]
  displayCurrency: CurrencyCode
  ratesToBase: Record<string, number>
}) {
  const totals = new Map<AccountKindGroup, number>()
  for (const account of accounts) {
    const group = accountKindGroup(account.kind)
    if (!group) continue
    const converted = convertCurrency(account.balance, account.currency, displayCurrency, ratesToBase)
    totals.set(group, (totals.get(group) ?? 0) + converted)
  }
  for (const asset of otherAssets) {
    const converted = convertCurrency(asset.value, asset.currency, displayCurrency, ratesToBase)
    totals.set('Other assets', (totals.get('Other assets') ?? 0) + converted)
  }

  const slices = GROUP_ORDER.map((group) => ({ group, signedValue: totals.get(group) ?? 0 }))
    .filter((slice) => slice.signedValue !== 0)
    .map((slice) => ({ ...slice, magnitude: Math.abs(slice.signedValue) }))
  const totalMagnitude = slices.reduce((sum, slice) => sum + slice.magnitude, 0)

  if (!slices.length || totalMagnitude === 0) return null

  return (
    <div className="space-y-2 pb-4">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {slices.map((slice) => (
          <div
            key={slice.group}
            style={{ width: `${(slice.magnitude / totalMagnitude) * 100}%`, backgroundColor: GROUP_COLORS[slice.group] }}
            title={`${slice.group}: ${formatCurrency(slice.signedValue, displayCurrency)} (${((slice.magnitude / totalMagnitude) * 100).toFixed(1)}%)`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {slices.map((slice) => (
          <span key={slice.group} className="flex items-center gap-1.5">
            <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: GROUP_COLORS[slice.group] }} />
            {slice.group} · {((slice.magnitude / totalMagnitude) * 100).toFixed(0)}% ·{' '}
            {formatCurrency(slice.signedValue, displayCurrency)}
          </span>
        ))}
      </div>
    </div>
  )
}
