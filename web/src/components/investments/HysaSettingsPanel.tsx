import { useMemo, useState } from 'react'
import { Brush, CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartCard } from '@/components/investments/ChartCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { colorForIndex } from '@/lib/colors'
import { formatDate } from '@/lib/format'
import { useHysaRates, useHysaSettings, useSetHysaSettings } from '@/hooks/usePortfolioData'
import type { HysaBank, HysaRatePoint } from '@/types/portfolio'

const CUSTOM_RATE = '__custom__'

function buildComparisonSeries(history: HysaRatePoint[] | undefined, selected: Set<string>) {
  if (!history || !selected.size) return []
  const relevant = history.filter((point) => selected.has(point.bank_id))
  const byBank = new Map<string, HysaRatePoint[]>()
  for (const point of relevant) {
    byBank.set(point.bank_id, [...(byBank.get(point.bank_id) ?? []), point])
  }
  for (const points of byBank.values()) points.sort((a, b) => a.rate_date.localeCompare(b.rate_date))
  const allDates = [...new Set(relevant.map((point) => point.rate_date))].sort()

  return allDates.map((currentDate) => {
    const row: Record<string, string | number> = { date: currentDate }
    for (const [bankId, points] of byBank) {
      const asOf = [...points].reverse().find((point) => point.rate_date <= currentDate)
      if (asOf) row[bankId] = asOf.apy_pct
    }
    return row
  })
}

// NEW_TASKS.md 2.2a: rate(t) must be a real time series, not a hardcoded
// 4% — this lets the user pick which bank's real history to compound
// against, or override with a fixed rate, and see how the candidates
// actually moved before choosing.
export function HysaSettingsPanel() {
  const { data: rates } = useHysaRates()
  const { data: settings } = useHysaSettings()
  const setSettings = useSetHysaSettings()
  const [showCompare, setShowCompare] = useState(false)
  const [selectedBanks, setSelectedBanks] = useState<Set<string>>(new Set())
  const [rateDraft, setRateDraft] = useState('')

  const isCustom = settings?.fixed_rate_pct != null
  const selectedValue = isCustom ? CUSTOM_RATE : (settings?.bank_id ?? rates?.default_bank_id ?? '')
  const comparisonData = useMemo(() => buildComparisonSeries(rates?.history, selectedBanks), [rates, selectedBanks])

  function handleBankChange(value: string | null) {
    if (value === CUSTOM_RATE) {
      setSettings.mutate({ bank_id: null, fixed_rate_pct: Number(rateDraft) || 0 })
    } else {
      setSettings.mutate({ bank_id: value, fixed_rate_pct: null })
    }
  }

  function applyCustomRate() {
    setSettings.mutate({ bank_id: null, fixed_rate_pct: Number(rateDraft) || 0 })
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
    <div className="space-y-3 rounded-xl p-4 ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">HYSA counterfactual rate</span>
        <Select value={selectedValue} onValueChange={handleBankChange}>
          <SelectTrigger size="sm" className="w-56">
            <SelectValue placeholder="Choose a bank" />
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
              defaultValue={settings?.fixed_rate_pct ?? undefined}
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
        <div className="space-y-3">
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
              <Tooltip
                formatter={(value, name) => [`${value}%`, name]}
                labelFormatter={(label) => formatDate(String(label))}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {[...selectedBanks].map((bankId, index) => (
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
              ))}
              <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
            </LineChart>
          </ChartCard>
        </div>
      )}
    </div>
  )
}
