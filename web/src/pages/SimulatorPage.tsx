import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency, formatCurrencyCompact } from '@/lib/format'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import {
  useAccountingStore,
  useNetWorth,
  useSetSimulatorScenarios,
  useSimulatorProjection,
} from '@/hooks/useAccountingData'
import type { CompoundingFrequency, SimulatorScenario } from '@/types/accounting'

const FREQUENCY_ITEMS: Record<CompoundingFrequency, string> = { annually: 'Annually', monthly: 'Monthly', daily: 'Daily' }
const NO_ACCOUNT = '__none__'

interface Inputs {
  initialCapital: string
  monthlyContribution: string
  horizonYears: string
  annualRatePct: string
  compoundingFrequency: CompoundingFrequency
}

function defaultInputs(): Inputs {
  return {
    initialCapital: '10000',
    monthlyContribution: '500',
    horizonYears: '20',
    annualRatePct: '6',
    compoundingFrequency: 'monthly',
  }
}

function scenarioToInputs(scenario: SimulatorScenario): Inputs {
  return {
    initialCapital: String(scenario.initial_capital),
    monthlyContribution: String(scenario.monthly_contribution),
    horizonYears: String(scenario.horizon_years),
    annualRatePct: String(scenario.annual_rate_pct),
    compoundingFrequency: scenario.compounding_frequency,
  }
}

// Ports the Finary calculator's five inputs (initial capital, monthly
// contribution, horizon, rate, compounding frequency) with saved scenarios
// on top — every projection is recomputed live from `dashboard.simulator`,
// never cached, since it's pure math with no ledger dependency.
export function SimulatorPage() {
  const { displayCurrency } = useDisplayCurrency()
  const [inputs, setInputs] = useState<Inputs>(defaultInputs())
  const [scenarioName, setScenarioName] = useState('')
  const [accountId, setAccountId] = useState(NO_ACCOUNT)

  const { data: netWorth } = useNetWorth(undefined, displayCurrency)
  const { data: store } = useAccountingStore()
  const setScenarios = useSetSimulatorScenarios()

  const initialCapital = Number.parseFloat(inputs.initialCapital) || 0
  const monthlyContribution = Number.parseFloat(inputs.monthlyContribution) || 0
  const horizonYears = Number.parseFloat(inputs.horizonYears) || 0
  const annualRatePct = Number.parseFloat(inputs.annualRatePct) || 0

  const { data: points, isLoading } = useSimulatorProjection(
    initialCapital,
    monthlyContribution,
    horizonYears,
    annualRatePct,
    inputs.compoundingFrequency,
  )

  const finalPoint = points?.[points.length - 1]
  const accountItems = {
    [NO_ACCOUNT]: 'None',
    ...Object.fromEntries((netWorth?.accounts ?? []).map((a) => [a.account_id, a.name])),
  }

  function update(patch: Partial<Inputs>) {
    setInputs({ ...inputs, ...patch })
  }

  function applyAccountDefault(id: string) {
    setAccountId(id)
    const account = netWorth?.accounts.find((a) => a.account_id === id)
    if (account) update({ initialCapital: String(Math.round(account.balance)) })
  }

  async function saveScenario() {
    if (!scenarioName.trim() || !store) return
    const scenario: SimulatorScenario = {
      scenario_id: `${Date.now()}-${scenarioName.trim().toLowerCase().replace(/\s+/g, '-')}`,
      name: scenarioName.trim(),
      initial_capital: initialCapital,
      monthly_contribution: monthlyContribution,
      horizon_years: horizonYears,
      annual_rate_pct: annualRatePct,
      compounding_frequency: inputs.compoundingFrequency,
      currency: displayCurrency,
    }
    await setScenarios.mutateAsync([...store.simulator_scenarios, scenario])
    setScenarioName('')
  }

  async function deleteScenario(scenarioId: string) {
    if (!store) return
    await setScenarios.mutateAsync(store.simulator_scenarios.filter((s) => s.scenario_id !== scenarioId))
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Simulator</h1>
      </div>

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        <Card>
          <CardHeader>
            <CardTitle>Compound growth projector</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Default from account
                <Select value={accountId} onValueChange={(value) => value && applyAccountDefault(value)}>
                  <SelectTrigger size="sm" className="w-44">
                    <SelectValue items={accountItems} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_ACCOUNT}>None</SelectItem>
                    {(netWorth?.accounts ?? []).map((account) => (
                      <SelectItem key={account.account_id} value={account.account_id}>
                        {account.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Initial capital
                <Input
                  type="number"
                  className="w-32"
                  value={inputs.initialCapital}
                  onChange={(e) => update({ initialCapital: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Monthly contribution
                <Input
                  type="number"
                  className="w-32"
                  value={inputs.monthlyContribution}
                  onChange={(e) => update({ monthlyContribution: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Horizon (years)
                <Input
                  type="number"
                  className="w-24"
                  value={inputs.horizonYears}
                  onChange={(e) => update({ horizonYears: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Annual rate (%)
                <Input
                  type="number"
                  className="w-24"
                  value={inputs.annualRatePct}
                  onChange={(e) => update({ annualRatePct: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Compounding
                <Select
                  value={inputs.compoundingFrequency}
                  onValueChange={(value) => value && update({ compoundingFrequency: value as CompoundingFrequency })}
                >
                  <SelectTrigger size="sm" className="w-32">
                    <SelectValue items={FREQUENCY_ITEMS} />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(FREQUENCY_ITEMS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            </div>

            {isLoading || !points ? (
              <Skeleton className="h-72 w-full" />
            ) : (
              <>
                {finalPoint && (
                  <div className="flex flex-wrap gap-6 text-sm">
                    <span>
                      Final balance:{' '}
                      <span className="font-semibold text-foreground">
                        {formatCurrency(finalPoint.balance, displayCurrency)}
                      </span>
                    </span>
                    <span className="text-muted-foreground">
                      Contributed: {formatCurrency(finalPoint.contributions_to_date, displayCurrency)}
                    </span>
                    <span className="text-muted-foreground">
                      Growth: {formatCurrency(finalPoint.balance - finalPoint.contributions_to_date, displayCurrency)}
                    </span>
                  </div>
                )}
                <ResponsiveContainer width="100%" height={288}>
                  <LineChart data={points} margin={{ left: 8, right: 8, top: 8 }}>
                    <CartesianGrid vertical={false} stroke="var(--border)" />
                    <XAxis
                      dataKey="month"
                      tickFormatter={(m) => `${Math.round(m / 12)}y`}
                      tick={{ fontSize: 12 }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tickFormatter={(v) => formatCurrencyCompact(v, displayCurrency)}
                      tick={{ fontSize: 12 }}
                      axisLine={false}
                      tickLine={false}
                      width={64}
                    />
                    <Tooltip
                      formatter={(value, name) => [
                        formatCurrency(Number(value), displayCurrency),
                        name === 'balance' ? 'Balance' : 'Contributed',
                      ]}
                      labelFormatter={(label) => `Month ${label}`}
                    />
                    <Line type="monotone" dataKey="balance" name="balance" stroke="#0f172a" strokeWidth={2} dot={false} />
                    <Line
                      type="monotone"
                      dataKey="contributions_to_date"
                      name="contributions_to_date"
                      stroke="#94a3b8"
                      strokeWidth={1.5}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </>
            )}

            <div className="flex items-end gap-2 border-t border-border pt-4">
              <Input
                className="w-48"
                placeholder="Scenario name"
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
              />
              <Button size="sm" onClick={saveScenario} disabled={!scenarioName.trim() || setScenarios.isPending}>
                Save as scenario
              </Button>
            </div>
          </CardContent>
        </Card>

        {store && store.simulator_scenarios.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Saved scenarios</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {store.simulator_scenarios.map((scenario) => (
                <Badge key={scenario.scenario_id} variant="outline" className="gap-1.5 py-1.5">
                  <button type="button" className="hover:underline" onClick={() => setInputs(scenarioToInputs(scenario))}>
                    {scenario.name}
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteScenario(scenario.scenario_id)}
                    className="text-muted-foreground/60 hover:text-destructive"
                  >
                    <Trash2 className="size-2.5" />
                  </button>
                </Badge>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
