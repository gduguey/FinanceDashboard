import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { PieChartLegend } from '@/components/shared/PieChartLegend'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { formatCurrency, formatCurrencyCompact } from '@/lib/format'
import type { CurrencyCode, Goal } from '@/types/accounting'

const UNALLOCATED_COLOR = '#94a3b8'

// The pie half of `GoalsOverviewCharts`, split out on its own so a page
// that wants goal balances next to a *different* pie (Overview swaps this
// one for `NetWorthAllocationPie`) can still reuse the matching bar chart
// below without also being stuck with this one.
export function GoalsBalancePieChart({
  goals,
  balances,
  unallocated,
  mode,
  displayCurrency,
}: {
  goals: Goal[]
  balances: Record<string, number>
  unallocated: number
  mode: 'all_time' | 'per_month'
  displayCurrency: CurrencyCode
}) {
  const pieSlices = [
    ...goals
      .filter((goal) => (balances[goal.goal_id] ?? 0) > 0)
      .map((goal) => ({ key: goal.goal_id, name: goal.name, value: balances[goal.goal_id] ?? 0, color: goal.color })),
    ...(unallocated > 0
      ? [{ key: 'unallocated', name: 'Unallocated', value: unallocated, color: UNALLOCATED_COLOR }]
      : []),
  ]
  const total = pieSlices.reduce((sum, slice) => sum + slice.value, 0)

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>{mode === 'all_time' ? 'Where goal money sits' : 'This month’s allocations'}</CardTitle>
        <CardDescription>
          {mode === 'all_time' ? 'Balances to date, including unallocated' : 'Contributions made this month'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {pieSlices.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">No data yet</div>
        ) : (
          <div className="flex gap-4">
            <ResponsiveContainer width="100%" height={260} className="flex-1">
              <PieChart>
                <Pie
                  data={pieSlices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={1}
                  label={false}
                >
                  {pieSlices.map((slice) => (
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
            <PieChartLegend total={total} slices={pieSlices} displayCurrency={displayCurrency} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// The bar half of `GoalsOverviewCharts` — each goal's current balance
// next to its target, independent of whichever pie chart it's paired
// with on a given page.
export function GoalsBalanceBarChart({
  goals,
  balances,
  targets,
  mode,
  displayCurrency,
}: {
  goals: Goal[]
  balances: Record<string, number>
  targets: Record<string, number>
  mode: 'all_time' | 'per_month'
  displayCurrency: CurrencyCode
}) {
  const barData = goals.map((goal) => ({
    name: goal.name,
    balance: balances[goal.goal_id] ?? 0,
    target: targets[goal.goal_id] ?? 0,
    color: goal.color,
  }))

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>Balance vs. target</CardTitle>
        <CardDescription>
          {mode === 'all_time'
            ? 'Current balance next to the final target amount'
            : "This month's contribution next to the linear pace needed to reach the target on time"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {barData.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">No goals yet</div>
        ) : (
          // Matches the pie chart's own fixed height so the two cards in
          // this grid row read as the same size instead of this one
          // looking short and off-center — only grows past that once
          // there are enough goals to actually need the extra room.
          <ResponsiveContainer width="100%" height={Math.max(260, barData.length * 56)}>
            <BarChart data={barData} layout="vertical" margin={{ left: 24, right: 16, top: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis
                type="number"
                domain={[0, 'auto']}
                tickFormatter={(value: number) => formatCurrencyCompact(value, displayCurrency)}
              />
              <YAxis type="category" dataKey="name" width={100} />
              <Tooltip
                formatter={(value, name) => [
                  formatCurrency(Number(value), displayCurrency),
                  name === 'balance' ? 'Balance' : 'Target',
                ]}
              />
              <Bar dataKey="balance" radius={[0, 4, 4, 0]}>
                {barData.map((row) => (
                  <Cell key={row.name} fill={row.color} />
                ))}
              </Bar>
              <Bar dataKey="target" fill="#93c5fd" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// Two views of the same underlying numbers, switched together by the
// all-time/per-month toggle one level up: "stock" (balances/target to
// date) or "flow" (this month's contributions/linear-pace target) — see
// `GoalsPage`'s own comment on how each is derived. Kept as the combined
// pair for `GoalsPage`; Overview uses `GoalsBalanceBarChart` on its own,
// paired with `NetWorthAllocationPie` instead of this pie.
export function GoalsOverviewCharts({
  goals,
  balances,
  unallocated,
  targets,
  mode,
  displayCurrency,
}: {
  goals: Goal[]
  balances: Record<string, number>
  unallocated: number
  targets: Record<string, number>
  mode: 'all_time' | 'per_month'
  displayCurrency: CurrencyCode
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <GoalsBalancePieChart
        goals={goals}
        balances={balances}
        unallocated={unallocated}
        mode={mode}
        displayCurrency={displayCurrency}
      />
      <GoalsBalanceBarChart
        goals={goals}
        balances={balances}
        targets={targets}
        mode={mode}
        displayCurrency={displayCurrency}
      />
    </div>
  )
}
