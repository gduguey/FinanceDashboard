import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { useAllocation, useSetTargetAllocation, useTargetAllocation } from '@/hooks/usePortfolioData'
import { formatPercent, formatUsd } from '@/lib/format'

// Sliced by current value (including cash), not invested dollars —
// invested-dollar slices can't show drift from a target. A horizontal bar
// reads target-vs-actual pairs more directly than two donuts.
//
// The chart and the drift numbers react to the draft target the moment you
// type it. Leaving a field (blur) persists the whole draft map, the same
// auto-save-on-blur pattern HysaSettingsPanel uses — there's no separate
// Save action to remember to click.
export function AllocationView() {
  const { data, isLoading, error } = useAllocation()
  const { data: targets } = useTargetAllocation()
  const setTargets = useSetTargetAllocation()
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  useEffect(() => {
    if (targets) setDrafts(Object.fromEntries(Object.entries(targets).map(([k, v]) => [k, String(v)])))
  }, [targets])

  const liveRows = useMemo(
    () =>
      data?.map((row) => {
        const draft = Number(drafts[row.symbol])
        const target_pct = drafts[row.symbol] !== undefined && Number.isFinite(draft) ? draft : row.target_pct
        return { ...row, target_pct, drift_pct: row.current_pct - target_pct }
      }),
    [data, drafts],
  )

  function commitDrafts() {
    const parsed = Object.fromEntries(
      Object.entries(drafts)
        .map(([symbol, value]) => [symbol, Number(value)] as const)
        .filter(([, value]) => !Number.isNaN(value)),
    )
    setTargets.mutate(parsed)
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartCard
        title="Allocation"
        description="Current value vs. target, by symbol (including cash)"
        isLoading={isLoading}
        isEmpty={!liveRows?.length}
        error={error?.message}
      >
        <BarChart data={liveRows} layout="vertical" margin={{ left: 8, right: 24, top: 8 }}>
          <CartesianGrid horizontal={false} stroke="var(--border)" />
          <XAxis
            type="number"
            tickFormatter={(v) => `${v}%`}
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="symbol"
            tick={{ fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={56}
          />
          <Tooltip formatter={(value, name) => [formatPercent(Number(value), 1), name]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="current_pct" name="Current %" fill="#0f172a" radius={2} />
          <Bar dataKey="target_pct" name="Target %" fill="#93c5fd" radius={2} />
        </BarChart>
      </ChartCard>

      <div className="rounded-xl ring-1 ring-foreground/10">
        <div className="p-4">
          <h3 className="text-base font-medium">Target allocation</h3>
          <p className="text-sm text-muted-foreground">Saves automatically as you edit each field.</p>
        </div>
        <div className="space-y-2 px-4 pb-4">
          {liveRows?.map((row) => (
            <div key={row.symbol} className="flex items-center justify-between gap-3 text-sm">
              <span className="w-16 font-medium">{row.symbol}</span>
              <span className="w-24 text-right tabular-nums text-muted-foreground">{formatUsd(row.value_usd)}</span>
              <span className="flex w-24 items-center justify-end gap-1 text-right tabular-nums text-muted-foreground">
                {formatPercent(row.drift_pct, 1)} drift
                <InfoTooltip term="drift" />
              </span>
              <input
                type="number"
                className="w-20 rounded-md border border-input bg-background px-2 py-1 text-right text-sm tabular-nums"
                value={drafts[row.symbol] ?? ''}
                onChange={(event) => setDrafts((prev) => ({ ...prev, [row.symbol]: event.target.value }))}
                onBlur={commitDrafts}
                placeholder="0"
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
