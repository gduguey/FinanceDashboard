import { Cell, Pie, PieChart, Tooltip } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { colorForIndex } from '@/lib/colors'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency } from '@/lib/format'
import type { CurrencyCode, NetWorthAccountRow, OtherAsset } from '@/types/accounting'

const LIABILITY_KINDS = new Set(['credit_card', 'loan'])

export function NetWorthAllocationPie({
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
  const accountSlices = accounts
    .filter((row) => !LIABILITY_KINDS.has(row.kind) && row.balance > 0)
    .map((row) => ({ name: row.name, value: convertCurrency(row.balance, row.currency, displayCurrency, ratesToBase) }))
  const otherAssetSlices = otherAssets
    .filter((asset) => asset.value > 0)
    .map((asset) => ({ name: asset.name, value: convertCurrency(asset.value, asset.currency, displayCurrency, ratesToBase) }))

  const slices = [...accountSlices, ...otherAssetSlices].sort((a, b) => b.value - a.value)
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)

  return (
    <ChartCard
      title="Where the money is"
      description="Assets and other assets by account, excluding liabilities"
      isEmpty={!slices.length}
    >
      <PieChart>
        <Pie data={slices} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={1} label={false}>
          {slices.map((slice, index) => (
            <Cell key={slice.name} fill={colorForIndex(index)} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value, name) => [
            `${formatCurrency(Number(value), displayCurrency)} (${((Number(value) / total) * 100).toFixed(1)}%)`,
            name,
          ]}
        />
      </PieChart>
    </ChartCard>
  )
}
