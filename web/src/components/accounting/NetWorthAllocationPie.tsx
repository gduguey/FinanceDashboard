import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { PieChartLegend } from '@/components/shared/PieChartLegend'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { colorForIndex } from '@/lib/colors'
import { convertCurrency } from '@/lib/currency'
import { formatCurrency } from '@/lib/format'
import type { CurrencyCode, NetWorthAccountRow, NetWorthOtherAssetRow } from '@/types/accounting'

const LIABILITY_KINDS = new Set(['credit_card', 'loan'])

export function NetWorthAllocationPie({
  accounts,
  otherAssets,
  displayCurrency,
  ratesToBase,
}: {
  accounts: NetWorthAccountRow[]
  otherAssets: NetWorthOtherAssetRow[]
  displayCurrency: CurrencyCode
  ratesToBase: Record<string, number>
}) {
  const accountSlices = accounts
    .filter((row) => !LIABILITY_KINDS.has(row.kind) && row.balance > 0)
    .map((row) => ({
      key: row.account_id,
      name: row.name,
      value: convertCurrency(row.balance, row.currency, displayCurrency, ratesToBase),
    }))
  const otherAssetSlices = otherAssets
    .filter((asset) => asset.value > 0)
    .map((asset) => ({
      key: asset.asset_id,
      name: asset.name,
      value: convertCurrency(asset.value, asset.currency, displayCurrency, ratesToBase),
    }))

  const slices = [...accountSlices, ...otherAssetSlices]
    .sort((a, b) => b.value - a.value)
    .map((slice, index) => ({ ...slice, color: colorForIndex(index) }))
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>Where the money is</CardTitle>
        <CardDescription>Assets and other assets by account, excluding liabilities</CardDescription>
      </CardHeader>
      <CardContent>
        {!slices.length ? (
          <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">No data yet</div>
        ) : (
          <div className="flex gap-4">
            <ResponsiveContainer width="100%" height={288} className="flex-1">
              <PieChart>
                <Pie
                  data={slices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={1}
                  label={false}
                >
                  {slices.map((slice) => (
                    <Cell key={slice.key} fill={slice.color} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value, name) => [
                    `${formatCurrency(Number(value), displayCurrency)} (${((Number(value) / total) * 100).toFixed(1)}%)`,
                    name,
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>
            <PieChartLegend total={total} slices={slices} displayCurrency={displayCurrency} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
