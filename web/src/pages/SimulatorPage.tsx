import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { DisplayCurrencyToggle } from '@/components/shared/DisplayCurrencyToggle'
import { lazyChart } from '@/components/shared/lazyChart'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useAccountingStore,
  useCreateSimulatorScenario,
  useDeleteSimulatorScenario,
  useNetWorth,
  useSimulatorProjection,
} from '@/hooks/useAccountingData'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import { formatCurrency } from '@/lib/format'
import type { CompoundingFrequency, SimulatorScenario } from '@/types/accounting'

// The simulator projection is the page's only chart; deferring it keeps
// recharts off this route's critical path. See `lazyChart`.
const ProjectionChart = lazyChart(
  () => import('@/components/investments/ProjectionChart').then((m) => m.ProjectionChart),
  'h-72 w-full',
)

const FREQUENCY_ITEMS: Record<CompoundingFrequency, string> = {
  annually: 'Annually',
  monthly: 'Monthly',
  daily: 'Daily',
}
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
  const deleteScenarioMutation = useDeleteSimulatorScenario()
  const createScenario = useCreateSimulatorScenario()

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
    await createScenario.mutateAsync({
      name: scenarioName.trim(),
      initial_capital: initialCapital,
      monthly_contribution: monthlyContribution,
      horizon_years: horizonYears,
      annual_rate_pct: annualRatePct,
      compounding_frequency: inputs.compoundingFrequency,
      currency: displayCurrency,
    })
    setScenarioName('')
  }

  async function deleteScenario(scenarioId: string) {
    await deleteScenarioMutation.mutateAsync(scenarioId)
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader
        title="Simulator"
        actions={
          <>
            <DisplayCurrencyToggle />
          </>
        }
      />

      <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
        <Card>
          <CardHeader>
            <CardTitle>Compound growth projector</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Default from account">
                {(id) => (
                  <Select value={accountId} onValueChange={(value) => value && applyAccountDefault(value)}>
                    <SelectTrigger id={id} size="sm" className="w-44">
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
                )}
              </Field>
              <Field label="Initial capital">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    className="w-32"
                    value={inputs.initialCapital}
                    onChange={(e) => update({ initialCapital: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Monthly contribution">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    className="w-32"
                    value={inputs.monthlyContribution}
                    onChange={(e) => update({ monthlyContribution: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Horizon (years)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    className="w-24"
                    value={inputs.horizonYears}
                    onChange={(e) => update({ horizonYears: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Annual rate (%)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    className="w-24"
                    value={inputs.annualRatePct}
                    onChange={(e) => update({ annualRatePct: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Compounding">
                {(id) => (
                  <Select
                    value={inputs.compoundingFrequency}
                    onValueChange={(value) => value && update({ compoundingFrequency: value as CompoundingFrequency })}
                  >
                    <SelectTrigger id={id} size="sm" className="w-32">
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
                )}
              </Field>
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
                <ProjectionChart points={points} displayCurrency={displayCurrency} />
              </>
            )}

            <div className="flex items-end gap-2 border-t border-border pt-4">
              <Input
                className="w-48"
                placeholder="Scenario name"
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
              />
              <Button size="sm" onClick={saveScenario} disabled={!scenarioName.trim() || createScenario.isPending}>
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
                  <button
                    type="button"
                    className="hover:underline"
                    onClick={() => setInputs(scenarioToInputs(scenario))}
                  >
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
