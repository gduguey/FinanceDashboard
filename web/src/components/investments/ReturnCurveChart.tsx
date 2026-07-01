import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { formatPercent } from '@/lib/format'
import { useReturnCurve } from '@/hooks/usePortfolioData'

export function ReturnCurveChart() {
  const { data, isLoading } = useReturnCurve()

  return (
    <ChartCard
      title="Annualized return vs. holding period"
      description="Short holds annualize into large, noisy numbers by design"
      isLoading={isLoading}
      isEmpty={!data?.points.length}
    >
      <ComposedChart margin={{ left: 8, right: 8, top: 8 }}>
        <CartesianGrid stroke="var(--border)" />
        <XAxis
          type="number"
          dataKey="days_held"
          name="Days held"
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          dataKey="annualized_return_pct"
          name="Annualized return"
          tickFormatter={(v) => `${v}%`}
          tick={{ fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={56}
        />
        <Tooltip
          formatter={(value, name) =>
            name === 'Annualized return' ? [formatPercent(Number(value)), name] : [value, name]
          }
          cursor={{ strokeDasharray: '3 3' }}
        />
        {data && (
          <ReferenceLine
            y={data.hysa_annual_rate_pct}
            stroke="#059669"
            strokeDasharray="4 4"
            label={{ value: `${data.hysa_annual_rate_pct.toFixed(0)}% HYSA`, fontSize: 11, fill: '#059669' }}
          />
        )}
        <Scatter data={data?.points} fill="#2563eb" fillOpacity={0.7} />
        <Line
          data={data?.trend}
          type="linear"
          dataKey="annualized_return_pct"
          stroke="#0f172a"
          strokeWidth={2}
          dot={false}
          legendType="none"
        />
      </ComposedChart>
    </ChartCard>
  )
}
