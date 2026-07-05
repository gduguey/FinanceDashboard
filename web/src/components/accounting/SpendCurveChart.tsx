import { useState } from 'react'
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { Input } from '@/components/ui/input'
import { formatCurrency } from '@/lib/format'
import { useSpendCurve } from '@/hooks/useAccountingData'
import type { CurrencyCode } from '@/types/accounting'

const CURRENT_MONTH = new Date().toISOString().slice(0, 7)

export function SpendCurveChart({ displayCurrency }: { displayCurrency: CurrencyCode }) {
  const [month, setMonth] = useState(CURRENT_MONTH)
  const { data, isLoading } = useSpendCurve(`${month}-01`, 3, displayCurrency)

  return (
    <ChartCard
      title="Monthly spend, day by day"
      description="Cumulative spend this month vs. the average of the previous 3 months"
      action={<Input type="month" className="w-36" value={month} onChange={(event) => setMonth(event.target.value)} />}
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
        <Tooltip formatter={(value) => formatCurrency(Number(value), displayCurrency)} labelFormatter={(label) => `Day ${label}`} />
        <Line type="monotone" dataKey="current_month_cumulative" name="This month" stroke="#0f172a" strokeWidth={2} dot={false} />
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
