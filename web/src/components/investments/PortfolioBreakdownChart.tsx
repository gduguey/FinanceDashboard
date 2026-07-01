import { useState } from 'react'
import { Cell, Legend, Pie, PieChart, Tooltip } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { colorForIndex } from '@/lib/colors'
import { formatUsd } from '@/lib/format'
import { usePieBreakdown } from '@/hooks/usePortfolioData'

export function PortfolioBreakdownChart() {
  const { data, isLoading } = usePieBreakdown()
  const labels = data ? Object.keys(data) : []
  const [selected, setSelected] = useState<string | null>(null)
  const activeLabel = selected && labels.includes(selected) ? selected : labels[0]
  const slices = data && activeLabel ? data[activeLabel] : []

  return (
    <ChartCard
      title="Portfolio breakdown"
      description="Invested USD, sliced by symbol or by date"
      isLoading={isLoading}
      isEmpty={!labels.length}
      action={
        labels.length > 0 && (
          <Select value={activeLabel} onValueChange={setSelected}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {labels.map((label) => (
                <SelectItem key={label} value={label}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )
      }
    >
      <PieChart>
        <Pie
          data={slices}
          dataKey="value"
          nameKey="name"
          innerRadius={60}
          outerRadius={100}
          paddingAngle={1}
          isAnimationActive={false}
        >
          {slices.map((slice, index) => (
            <Cell key={slice.name} fill={colorForIndex(index)} />
          ))}
        </Pie>
        <Tooltip formatter={(value) => formatUsd(Number(value))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ChartCard>
  )
}
