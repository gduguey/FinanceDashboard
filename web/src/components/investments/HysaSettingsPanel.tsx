import { useEffect, useMemo, useRef, useState } from 'react'
import type { TooltipContentProps } from 'recharts'
import { Brush, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/shared/ChartCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { colorForIndex } from '@/lib/colors'
import { formatDate } from '@/lib/format'
import { useHysaRates, useHysaSettings, useSetHysaSettings } from '@/hooks/usePortfolioData'
import type { HysaBank, HysaRatePoint } from '@/types/portfolio'

const CUSTOM_RATE = '__custom__'

function buildComparisonSeries(
  history: HysaRatePoint[] | undefined,
  selected: Set<string>,
  customRatePct: number | null,
) {
  const relevant = (history ?? []).filter((point) => selected.has(point.bank_id))
  const byBank = new Map<string, HysaRatePoint[]>()
  for (const point of relevant) {
    byBank.set(point.bank_id, [...(byBank.get(point.bank_id) ?? []), point])
  }
  for (const points of byBank.values()) points.sort((a, b) => a.rate_date.localeCompare(b.rate_date))
  const allDates = [...new Set(relevant.map((point) => point.rate_date))].sort()
  if (!allDates.length) return []

  const includeCustom = customRatePct !== null && selected.has(CUSTOM_RATE)
  return allDates.map((currentDate) => {
    const row: Record<string, string | number> = { date: currentDate }
    for (const [bankId, points] of byBank) {
      const asOf = [...points].reverse().find((point) => point.rate_date <= currentDate)
      if (asOf) row[bankId] = asOf.apy_pct
    }
    if (includeCustom) row[CUSTOM_RATE] = customRatePct
    return row
  })
}

// Sorts hover entries highest-APY-first, so comparing banks at a glance
// doesn't require matching colors to a separate legend.
function ApyTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null
  const sorted = [...payload].sort((a, b) => Number(b.value ?? 0) - Number(a.value ?? 0))
  return (
    <div className="min-w-40 rounded-md border border-border bg-popover p-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{formatDate(String(label))}</div>
      {sorted.map((entry) => (
        <div key={entry.name} className="flex justify-between gap-3">
          <span style={{ color: entry.color }}>{entry.name}</span>
          <span className="tabular-nums">{Number(entry.value).toFixed(2)}%</span>
        </div>
      ))}
    </div>
  )
}

// rate(t) must be a real time series, not a hardcoded 4% — this lets the
// user pick which bank's real history to compound against, or override
// with a fixed rate, and see how the candidates actually moved before
// choosing. Rendered as a fragment (not one wrapping div) so the compact
// selector row can sit inline next to the benchmark picker while the
// comparison chart still breaks onto its own full-width row when open.
export function HysaSettingsPanel() {
  const { data: rates } = useHysaRates()
  const { data: settings } = useHysaSettings()
  const setSettings = useSetHysaSettings()
  const [showCompare, setShowCompare] = useState(false)
  const [selectedBanks, setSelectedBanks] = useState<Set<string>>(new Set())
  const [rateDraft, setRateDraft] = useState<string>('')
  const seededRef = useRef(false)

  const isCustom = settings?.fixed_rate_pct != null
  const selectedValue = isCustom ? CUSTOM_RATE : (settings?.bank_id ?? rates?.default_bank_id ?? '')

  // Compare-banks defaults to every known bank checked, so the chart is
  // immediately useful — seeded once when the bank list first loads, not
  // re-applied if the user later unchecks everything.
  useEffect(() => {
    if (!seededRef.current && rates?.banks.length) {
      setSelectedBanks(new Set(rates.banks.map((bank) => bank.bank_id)))
      seededRef.current = true
    }
  }, [rates])

  // Seed rateDraft when custom rate is loaded or selected
  useEffect(() => {
    if (isCustom && settings?.fixed_rate_pct != null) {
      setRateDraft(settings.fixed_rate_pct.toString())
    }
  }, [isCustom, settings?.fixed_rate_pct])

  const comparisonData = useMemo(
    () => buildComparisonSeries(rates?.history, selectedBanks, isCustom ? (settings?.fixed_rate_pct ?? null) : null),
    [rates, selectedBanks, isCustom, settings?.fixed_rate_pct],
  )

  function handleBankChange(value: string | null) {
    if (value === CUSTOM_RATE) {
      // When switching to custom, use the current rate or a sensible default
      const parsed = rateDraft.trim() ? Number(rateDraft) : settings?.fixed_rate_pct ?? 4.0
      setRateDraft(parsed.toString())
      setSettings.mutate({ bank_id: null, fixed_rate_pct: parsed })
    } else {
      setSettings.mutate({ bank_id: value, fixed_rate_pct: null })
    }
  }

  function applyCustomRate() {
    // Only submit if the draft was actually edited to a valid number
    const trimmed = rateDraft.trim()
    if (!trimmed) return // Blur without edits — preserve existing rate
    const parsed = Number(trimmed)
    if (!isNaN(parsed) && parsed >= 0) {
      setSettings.mutate({ bank_id: null, fixed_rate_pct: parsed })
    }
  }

  function toggleBank(bankId: string) {
    setSelectedBanks((prev) => {
      const next = new Set(prev)
      if (next.has(bankId)) next.delete(bankId)
      else next.add(bankId)
      return next
    })
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">HYSA counterfactual rate</span>
        <Select value={selectedValue} onValueChange={handleBankChange}>
          <SelectTrigger size="sm" className="w-56">
            <SelectValue
              placeholder="Choose a bank"
              items={{
                ...Object.fromEntries((rates?.banks ?? []).map((bank: HysaBank) => [bank.bank_id, bank.bank_name])),
                [CUSTOM_RATE]: 'Custom fixed rate…',
              }}
            />
          </SelectTrigger>
          <SelectContent>
            {rates?.banks.map((bank: HysaBank) => (
              <SelectItem key={bank.bank_id} value={bank.bank_id}>
                {bank.bank_name}
              </SelectItem>
            ))}
            <SelectItem value={CUSTOM_RATE}>Custom fixed rate…</SelectItem>
          </SelectContent>
        </Select>
        {isCustom && (
          <div className="flex items-center gap-1">
            <Input
              type="number"
              className="w-20"
              value={rateDraft}
              onChange={(event) => setRateDraft(event.target.value)}
              onBlur={applyCustomRate}
              placeholder="4.0"
            />
            <span className="text-sm text-muted-foreground">% APY</span>
          </div>
        )}
        <Button variant="outline" size="sm" onClick={() => setShowCompare((v) => !v)}>
          {showCompare ? 'Hide bank comparison' : 'Compare banks'}
        </Button>
      </div>

      {showCompare && (
        <div className="w-full space-y-3">
          <div className="flex flex-wrap gap-3 text-sm text-muted-foreground">
            {rates?.banks.map((bank: HysaBank) => (
              <label key={bank.bank_id} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={selectedBanks.has(bank.bank_id)}
                  onChange={() => toggleBank(bank.bank_id)}
                />
                {bank.bank_name}
              </label>
            ))}
            {isCustom && (
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={selectedBanks.has(CUSTOM_RATE)}
                  onChange={() => toggleBank(CUSTOM_RATE)}
                />
                Custom ({settings?.fixed_rate_pct}%)
              </label>
            )}
          </div>
          <ChartCard
            title="APY history"
            description="Real published rates over time for the banks checked above"
            isEmpty={!comparisonData.length}
          >
            <LineChart data={comparisonData} margin={{ left: 8, right: 8, top: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="date"
                tickFormatter={formatDate}
                tick={{ fontSize: 12 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={(v) => `${v}%`}
                tick={{ fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip content={ApyTooltip} />
              {[...selectedBanks].map((bankId, index) =>
                bankId === CUSTOM_RATE ? (
                  <Line
                    key={CUSTOM_RATE}
                    type="stepAfter"
                    dataKey={CUSTOM_RATE}
                    name="Custom"
                    stroke={colorForIndex(index)}
                    strokeWidth={2}
                    strokeDasharray="4 4"
                    dot={false}
                    connectNulls
                  />
                ) : (
                  <Line
                    key={bankId}
                    type="stepAfter"
                    dataKey={bankId}
                    name={rates?.banks.find((bank) => bank.bank_id === bankId)?.bank_name ?? bankId}
                    stroke={colorForIndex(index)}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                  />
                ),
              )}
              <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
            </LineChart>
          </ChartCard>
        </div>
      )}
    </>
  )
}
