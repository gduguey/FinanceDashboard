import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PieChartLegend } from '@/components/shared/PieChartLegend'
import { formatCurrency } from '@/lib/format'
import type { Goal } from '@/types/accounting'

const UNALLOCATED_COLOR = '#94a3b8'

// Two views of the same underlying numbers, switched together by the
// all-time/per-month toggle one level up: "stock" (balances/target to
// date) or "flow" (this month's contributions/linear-pace target) — see
// `GoalsPage`'s own comment on how each is derived.
export function GoalsOverviewCharts({
  goals,
  balances,
  unallocated,
  targets,
  mode,
}: {
  goals: Goal[]
  balances: Record<string, number>
  unallocated: number
  targets: Record<string, number>
  mode: 'all_time' | 'per_month'
}) {
  const pieSlices = [
    ...goals
      .filter((goal) => (balances[goal.goal_id] ?? 0) > 0)
      .map((goal) => ({ key: goal.goal_id, name: goal.name, value: balances[goal.goal_id] ?? 0, color: goal.color })),
    ...(unallocated > 0 ? [{ key: 'unallocated', name: 'Unallocated', value: unallocated, color: UNALLOCATED_COLOR }] : []),
  ]
  const total = pieSlices.reduce((sum, slice) => sum + slice.value, 0)

  const barData = goals.map((goal) => ({
    name: goal.name,
    balance: balances[goal.goal_id] ?? 0,
    target: targets[goal.goal_id] ?? 0,
    color: goal.color,
  }))

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="gap-3">
        <CardHeader>
          <CardTitle>{mode === 'all_time' ? 'Where goal money sits' : 'This month’s allocations'}</CardTitle>
          <CardDescription>{mode === 'all_time' ? 'Balances to date, including unallocated' : 'Contributions made this month'}</CardDescription>
        </CardHeader>
        <CardContent>
          {pieSlices.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">No data yet</div>
          ) : (
            <div className="flex gap-4">
              <ResponsiveContainer width="100%" height={260} className="flex-1">
                <PieChart>
                  <Pie data={pieSlices} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={1} label={false}>
                    {pieSlices.map((slice) => (
                      <Cell key={slice.key} fill={slice.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value, name) => [
                      `${formatCurrency(Number(value), 'USD')} (${((Number(value) / total) * 100).toFixed(1)}%)`,
                      name,
                    ]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <PieChartLegend total={total} slices={pieSlices} displayCurrency="USD" />
            </div>
          )}
        </CardContent>
      </Card>

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
            <ResponsiveContainer width="100%" height={Math.max(120, barData.length * 56)}>
              <BarChart data={barData} layout="vertical" margin={{ left: 24 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={(value: number) => formatCurrency(value, 'USD')} />
                <YAxis type="category" dataKey="name" width={100} />
                <Tooltip formatter={(value, name) => [formatCurrency(Number(value), 'USD'), name === 'balance' ? 'Balance' : 'Target']} />
                <Bar dataKey="balance" radius={[0, 4, 4, 0]}>
                  {barData.map((row) => (
                    <Cell key={row.name} fill={row.color} />
                  ))}
                </Bar>
                <Bar dataKey="target" fill="#e2e8f0" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
