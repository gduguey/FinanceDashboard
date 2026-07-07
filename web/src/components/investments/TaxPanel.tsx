import { useState } from 'react'
import { Landmark } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatUsd, signColor } from '@/lib/format'
import { useSetTaxSettings, useTaxReport, useTaxSettings } from '@/hooks/usePortfolioData'
import type { AnnualTaxRow, SalePreviewRow, TaxOwedRow, TaxRegime, TaxSettingsUpdate, WashSaleRow } from '@/types/portfolio'

const REGIME_LABELS: Record<TaxRegime, string> = {
  NRA: 'NRA / F-1 (nonresident alien)',
  RESIDENT: 'H-1B (resident alien)',
}

function regimeRules(regime: TaxRegime, w8benClaimed: boolean): { label: string; text: string }[] {
  if (regime === 'NRA') {
    return [
      {
        label: 'Dividends',
        text: w8benClaimed
          ? 'Taxed at your claimed tax-treaty rate, withheld automatically by your broker the moment each dividend is paid.'
          : 'Taxed at a flat 30% withholding rate, withheld automatically by your broker the moment each dividend is paid — filing Form W-8BEN to claim a tax-treaty rate can lower this.',
      },
      {
        label: 'Capital gains',
        text: 'Generally not taxed by the U.S. at all on security sales, as long as you are present in the U.S. fewer than 183 days in the tax year — one of the largest advantages of nonresident status.',
      },
      {
        label: 'Interest',
        text: 'Bank and portfolio interest paid to a nonresident alien is generally exempt from U.S. tax entirely.',
      },
      {
        label: 'Aggregation & losses',
        text: 'Since capital gains usually are not taxed at all, netting rarely comes up — but if the 183-day rule ever makes them taxable, the same calendar-year netting and 30-day rule described under H-1B would apply.',
      },
    ]
  }
  return [
    {
      label: 'Dividends',
      text: "Qualified dividends — from a U.S. or qualifying foreign company, held more than 60 days around the ex-dividend date — are taxed at the lower long-term capital-gains rate. Everything else (ordinary dividends, interest) is taxed at your regular income rate.",
    },
    {
      label: 'Capital gains',
      text: 'Capital gain = sale proceeds − cost basis (what you paid, including fees). A lot held over 365 days before selling is taxed at the lower long-term rate; 365 days or less is taxed at your regular income rate, the same as wages.',
    },
    {
      label: 'Aggregation period',
      text: 'Every sale in a calendar year is netted together at year end — a loss in one lot directly offsets a gain in another within the same year, long-term and short-term counted separately.',
    },
    {
      label: 'The 30-day rule',
      text: 'Sell at a loss, then buy the same — or a "substantially identical" — security within 30 days before or after that sale, and the loss is disallowed. It exists so a loss can\'t be claimed for tax purposes while functionally keeping the same position; the disallowed amount isn\'t lost, it\'s added to the cost basis of the repurchased shares instead.',
    },
  ]
}

export function RulesCard({ regime, w8benClaimed }: { regime: TaxRegime; w8benClaimed: boolean }) {
  const rules = regimeRules(regime, w8benClaimed)
  return (
    <Card className="border-foreground/10 bg-gradient-to-br from-muted/60 to-transparent">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Landmark className="size-4 text-muted-foreground" />
          How {REGIME_LABELS[regime]} status is taxed here
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-4 text-sm leading-relaxed text-muted-foreground md:grid-cols-2">
          {rules.map((rule) => (
            <div key={rule.label}>
              <dt className="font-medium text-foreground">{rule.label}</dt>
              <dd className="mt-0.5">{rule.text}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

// Uncontrolled-with-a-touched-flag input: `draft` starts `null` (untouched)
// so tabbing through the bar without typing anything can never overwrite a
// saved override with an empty value — only committed once the user has
// actually typed something and then leaves the field.
function RateField({
  label,
  override,
  resolvedPct,
  onCommit,
}: {
  label: string
  override: number | null
  resolvedPct: number
  onCommit: (pct: number | null) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  return (
    <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
      {label}
      <Input
        type="number"
        className="w-16"
        defaultValue={override ?? undefined}
        placeholder={resolvedPct.toFixed(0)}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft === null) return
          onCommit(draft.trim() === '' ? null : Number(draft))
          setDraft(null)
        }}
      />
      %
    </label>
  )
}

// Shared by both `TaxEnabledToggle` and `TaxRegimeSelector` — a plain
// object rather than re-deriving from `settings` at each call site.
function useTaxSettingsUpdater() {
  const { data: settings } = useTaxSettings()
  const setSettings = useSetTaxSettings()
  return function update(partial: Partial<TaxSettingsUpdate>) {
    if (!settings) return
    setSettings.mutate({
      tax_enabled: settings.tax_enabled,
      tax_regime: settings.tax_regime,
      residency_status_change_date: settings.residency_status_change_date,
      w8ben_claimed: settings.w8ben_claimed,
      w8ben_treaty_rate_pct: settings.w8ben_treaty_rate_pct,
      marginal_ordinary_rate_pct: settings.marginal_ordinary_rate_pct,
      qualified_ltcg_rate_pct: settings.qualified_ltcg_rate_pct,
      ...partial,
    })
  }
}

// Just the on/off switch — shown on Performance and Allocation, the two
// pages whose own figures change shape (after-tax dollar chart, lot
// tax-lot detail) depending on it. The full regime/rate configuration
// lives only on the Taxes page itself (see `TaxRegimeSelector`) since
// picking a regime is a taxes-specific concern, not something every page
// that merely respects the toggle needs to expose.
export function TaxEnabledToggle() {
  const { data: settings, isLoading } = useTaxSettings()
  const update = useTaxSettingsUpdater()

  if (isLoading) return <Skeleton className="h-8 w-32" />
  if (!settings) return null

  return (
    <label className="flex items-center gap-2 text-sm font-medium">
      Apply taxes
      <Switch checked={settings.tax_enabled} onCheckedChange={(checked) => update({ tax_enabled: checked })} />
      <InfoTooltip term="taxToggle" />
    </label>
  )
}

// The regime/rate configuration — shown only on the Taxes page, above its
// own tab selector, unconditional on the on/off toggle since the Taxes
// page always shows its report regardless of whether other pages are
// currently applying after-tax figures.
export function TaxRegimeSelector() {
  const { data: settings, isLoading } = useTaxSettings()
  const update = useTaxSettingsUpdater()

  if (isLoading) return <Skeleton className="h-10 w-full max-w-xl" />
  if (!settings) return null

  const regime = settings.tax_regime ?? settings.resolved_tax_regime
  const isNra = regime === 'NRA'

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Select value={regime} onValueChange={(value) => value && update({ tax_regime: value as TaxRegime })}>
        <SelectTrigger size="sm" className="w-56">
          <SelectValue items={REGIME_LABELS} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="NRA">NRA / F-1 (nonresident alien)</SelectItem>
          <SelectItem value="RESIDENT">H-1B (resident alien)</SelectItem>
        </SelectContent>
      </Select>
      <RateField
        label="Marginal rate"
        override={settings.marginal_ordinary_rate_pct}
        resolvedPct={settings.resolved_marginal_ordinary_rate_pct}
        onCommit={(pct) => update({ marginal_ordinary_rate_pct: pct })}
      />
      <RateField
        label="LTCG / qualified div. rate"
        override={settings.qualified_ltcg_rate_pct}
        resolvedPct={settings.resolved_qualified_ltcg_rate_pct}
        onCommit={(pct) => update({ qualified_ltcg_rate_pct: pct })}
      />
      {isNra && (
        <>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            W-8BEN treaty benefits
            <Switch checked={settings.w8ben_claimed} onCheckedChange={(checked) => update({ w8ben_claimed: checked })} />
          </label>
          {settings.w8ben_claimed && (
            <RateField
              label="Treaty rate"
              override={settings.w8ben_treaty_rate_pct}
              resolvedPct={settings.w8ben_treaty_rate_pct ?? 30}
              onCommit={(pct) => update({ w8ben_treaty_rate_pct: pct })}
            />
          )}
        </>
      )}
    </div>
  )
}

function AnnualReportTable({ rows }: { rows: AnnualTaxRow[] }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No realized gains or dividends yet.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Year</TableHead>
          <TableHead>
            Regime <InfoTooltip term="taxRegime" />
          </TableHead>
          <TableHead className="text-right">Long-term gain</TableHead>
          <TableHead className="text-right">Short-term gain</TableHead>
          <TableHead className="text-right">Qualified div.</TableHead>
          <TableHead className="text-right">Ordinary div.</TableHead>
          <TableHead className="text-right">Ordinary interest</TableHead>
          <TableHead className="text-right">Withholding</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.year}-${row.regime}`}>
            <TableCell className="font-medium">{row.year}</TableCell>
            <TableCell>
              <Badge variant="outline">{row.regime}</Badge>
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(row.long_term_gain_usd)}`}>
              {formatUsd(row.long_term_gain_usd)}
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(row.short_term_gain_usd)}`}>
              {formatUsd(row.short_term_gain_usd)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.qualified_dividends_usd)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.ordinary_dividends_usd)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.ordinary_interest_usd)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.withholding_tax_usd)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function TaxOwedTable({ rows }: { rows: TaxOwedRow[] }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">Nothing to estimate yet.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Year</TableHead>
          <TableHead>Regime</TableHead>
          <TableHead className="text-right">Capital-gains tax</TableHead>
          <TableHead className="text-right">Dividend/interest tax</TableHead>
          <TableHead className="text-right">Total estimated tax</TableHead>
          <TableHead className="text-right">Already withheld</TableHead>
          <TableHead className="text-right">Balance due</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.year}-${row.regime}`}>
            <TableCell className="font-medium">{row.year}</TableCell>
            <TableCell>
              <Badge variant="outline">{row.regime}</Badge>
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.capital_gains_tax_usd)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.dividend_tax_usd)}</TableCell>
            <TableCell className="text-right tabular-nums font-medium">{formatUsd(row.total_tax_usd)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.withholding_tax_usd)}</TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(-row.balance_due_usd)}`}>
              {formatUsd(row.balance_due_usd)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function WashSaleTable({ rows }: { rows: WashSaleRow[] }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No flagged wash sales.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Closed</TableHead>
          <TableHead className="text-right">Shares</TableHead>
          <TableHead className="text-right">Realized loss</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.lot_id}>
            <TableCell className="font-medium">{row.symbol}</TableCell>
            <TableCell>{row.closed_at.slice(0, 10)}</TableCell>
            <TableCell className="text-right tabular-nums">{row.shares.toFixed(4)}</TableCell>
            <TableCell className="text-right tabular-nums text-rose-600">{formatUsd(row.realized_gain)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function SalePreviewTable({ rows }: { rows: SalePreviewRow[] }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No open positions to preview.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead className="text-right">Shares</TableHead>
          <TableHead className="text-right">Days held</TableHead>
          <TableHead>If sold today</TableHead>
          <TableHead className="text-right">Unrealized gain</TableHead>
          <TableHead>
            Wash-sale risk <InfoTooltip term="washSaleFlag" />
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.lot_id}>
            <TableCell className="font-medium">{row.symbol}</TableCell>
            <TableCell className="text-right tabular-nums">{row.shares.toFixed(4)}</TableCell>
            <TableCell className="text-right tabular-nums">{row.days_held}</TableCell>
            <TableCell>
              <Badge variant="outline">{row.term}</Badge>
            </TableCell>
            <TableCell className={`text-right tabular-nums ${signColor(row.unrealized_gain_usd)}`}>
              {formatUsd(row.unrealized_gain_usd)}
            </TableCell>
            <TableCell>
              {row.would_wash_sale ? (
                <Badge variant="outline" className="border-amber-500 text-amber-600">
                  Would flag
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// The report tables and summary cards — the Taxes page's own "Report" tab.
// Always shown regardless of the Performance/Allocation on/off toggle: the
// backend computes this report unconditionally off the resolved regime, so
// there's no reason to hide it here too.
export function TaxReportTab() {
  const { data: settings } = useTaxSettings()
  const { data: report } = useTaxReport()

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Annual realized gains &amp; dividends</CardTitle>
        </CardHeader>
        <CardContent>
          <AnnualReportTable rows={report?.annual ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            Estimated tax owed <InfoTooltip term="taxOwed" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TaxOwedTable rows={report?.tax_owed ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            Flagged wash sales <InfoTooltip term="washSaleFlag" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <WashSaleTable rows={report?.wash_sales ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>If you sold today</CardTitle>
        </CardHeader>
        <CardContent>
          <SalePreviewTable rows={report?.sale_previews ?? []} />
        </CardContent>
      </Card>

      {report && settings && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              Liquidation value (after tax) <InfoTooltip term="liquidationValue" />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
                {formatUsd(report.liquidation_value_usd)}
              </span>
              <span className="text-sm text-muted-foreground">
                = {formatUsd(report.liquidation_pretax_value_usd)} portfolio value −{' '}
                {formatUsd(report.liquidation_capital_gains_tax_usd)} estimated capital-gains tax
              </span>
            </div>
            <div className="grid grid-cols-1 gap-4 border-t border-border pt-3 text-sm sm:grid-cols-3">
              <div>
                <div className="text-xs text-muted-foreground">Long-term gain × rate</div>
                <div className="tabular-nums">
                  {formatUsd(report.liquidation_long_term_gain_usd)} ×{' '}
                  {settings.resolved_qualified_ltcg_rate_pct.toFixed(1)}% ={' '}
                  {formatUsd((report.liquidation_long_term_gain_usd * settings.resolved_qualified_ltcg_rate_pct) / 100)}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Short-term gain × rate</div>
                <div className="tabular-nums">
                  {formatUsd(report.liquidation_short_term_gain_usd)} ×{' '}
                  {settings.resolved_marginal_ordinary_rate_pct.toFixed(1)}% ={' '}
                  {formatUsd(
                    (report.liquidation_short_term_gain_usd * settings.resolved_marginal_ordinary_rate_pct) / 100,
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Total estimated tax</div>
                <div className="tabular-nums font-medium">{formatUsd(report.liquidation_capital_gains_tax_usd)}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
