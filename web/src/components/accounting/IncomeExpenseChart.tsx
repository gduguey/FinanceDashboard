import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { useMonthlyIncomeExpense } from '@/hooks/useAccountingData'
import { formatCurrency, formatMonth } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

function twelveMonthsAgo(): string {
  const date = new Date()
  date.setMonth(date.getMonth() - 11)
  date.setDate(1)
  return date.toISOString().slice(0, 10)
}

const TODAY = new Date().toISOString().slice(0, 10)

export function IncomeExpenseChart({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const { data, isLoading } = useMonthlyIncomeExpense(twelveMonthsAgo(), TODAY, displayCurrency)

  return (
    <ChartCard
      title="Income vs. expenses per month"
      description="Last 12 months"
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <BarChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis
          tickFormatter={(v) => formatCurrency(v, displayCurrency)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={72}
        />
        <Tooltip
          formatter={(value) => formatCurrency(Number(value), displayCurrency)}
          labelFormatter={(label) => formatMonth(String(label))}
        />
        <Legend />
        <Bar dataKey="income" name="Income" fill="#059669" radius={[3, 3, 0, 0]} />
        <Bar dataKey="expense" name="Expense" fill="#dc2626" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ChartCard>
  )
}
