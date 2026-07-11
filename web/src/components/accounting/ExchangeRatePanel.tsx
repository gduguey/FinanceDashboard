import { Brush, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ExchangeRateSyncButton } from '@/components/shared/ExchangeRateSyncButton'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useCurrencies, useCurrentExchangeRate, useExchangeRateHistory } from '@/hooks/useAccountingData'
import { formatDate } from '@/lib/format'
import type { CurrencyCode } from '@/types/accounting'

const BASE_CURRENCY: CurrencyCode = 'USD'

function OneCurrencyPanel({ currency }: { currency: CurrencyCode }) {
  const { data: history, isLoading, isError, error } = useExchangeRateHistory(currency)
  const { data: current } = useCurrentExchangeRate(currency)

  if (isLoading) return <Skeleton className="h-72 w-full" />
  if (isError || !history?.length) {
    return (
      <p className="flex h-72 items-center justify-center px-6 text-center text-sm text-muted-foreground">
        {error?.message || `No ${currency}/${BASE_CURRENCY} history yet — click Sync above.`}
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {current && (
        <p className="text-sm text-muted-foreground">
          Value used for {currency} conversions:{' '}
          <span className="font-medium text-foreground">
            1 {currency} = {current.rate_to_base.toFixed(4)} {BASE_CURRENCY}
          </span>{' '}
          — the {current.window_days}-day trailing average of the daily rate as of {formatDate(current.as_of)}, not
          today's spot rate, so net worth doesn't swing on day-to-day FX noise.
        </p>
      )}
      <ResponsiveContainer width="100%" height={288}>
        <LineChart data={history} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis domain={['auto', 'auto']} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} width={56} />
          <Tooltip
            formatter={(value, name) => [Number(value).toFixed(4), name]}
            labelFormatter={(label) => formatDate(String(label))}
          />
          <Line type="monotone" dataKey="rate" name="Daily rate" stroke="#94a3b8" strokeWidth={1} dot={false} />
          <Line
            type="monotone"
            dataKey="smoothed_rate"
            name="30-day average (used for conversions)"
            stroke="#0f172a"
            strokeWidth={2}
            dot={false}
          />
          <Brush dataKey="date" height={20} tickFormatter={formatDate} stroke="#94a3b8" travellerWidth={8} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function ExchangeRatePanel() {
  const { data: currencies } = useCurrencies()
  const nonBaseCurrencies = (currencies ?? []).filter((currency) => currency.code !== BASE_CURRENCY)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Exchange rates</CardTitle>
        <CardDescription>
          Every rate over the last 2 years, pulled from the European Central Bank via Frankfurter — the same source IAS
          21 "average rate" translations are built on.
        </CardDescription>
        <CardAction>
          <ExchangeRateSyncButton />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-6">
        {nonBaseCurrencies.length === 0 ? (
          <p className="text-sm text-muted-foreground">Loading supported currencies…</p>
        ) : (
          nonBaseCurrencies.map((currency) => <OneCurrencyPanel key={currency.code} currency={currency.code} />)
        )}
      </CardContent>
    </Card>
  )
}
