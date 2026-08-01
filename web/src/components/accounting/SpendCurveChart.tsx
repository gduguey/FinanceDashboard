import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { MonthSelect } from '@/components/accounting/MonthSelect'
import { ChartCard } from '@/components/shared/ChartCard'
import { usePostingMonths, useSpendCurve } from '@/hooks/useAccountingData'
import { usePersistedState } from '@/hooks/usePersistedState'
import { formatCurrency } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

// Local calendar, not UTC `toISOString()` — a user near a month boundary in a
// negative-UTC timezone should default to their own current month.
const _now = new Date()
const CURRENT_MONTH = `${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, '0')}`

export function SpendCurveChart({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const [month, setMonth] = usePersistedState('accounting.spend-curve-month', CURRENT_MONTH)
  const { data, isLoading } = useSpendCurve(`${month}-01`, 3, displayCurrency)
  const { data: months } = usePostingMonths()

  return (
    <ChartCard
      title="Monthly spend, day by day"
      description="Cumulative spend this month vs. the average of the previous 3 months"
      action={<MonthSelect value={month} onChange={setMonth} months={months ?? []} className="w-40" />}
      isLoading={isLoading}
      isEmpty={!data?.length}
    >
      <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="day" tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis
          tickFormatter={(v) => formatCurrency(v, displayCurrency)}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={72}
        />
        <Tooltip
          formatter={(value) =>
            value == null ? 'Not enough history yet' : formatCurrency(Number(value), displayCurrency)
          }
          labelFormatter={(label) => `Day ${label}`}
        />
        <Line
          type="monotone"
          dataKey="current_month_cumulative"
          name="This month"
          stroke="#0f172a"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="average_previous_months_cumulative"
          name="Avg. last 3 months"
          stroke="#94a3b8"
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
        />
      </LineChart>
    </ChartCard>
  )
}
