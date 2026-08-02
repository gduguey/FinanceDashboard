import { Info } from 'lucide-react'
import { useExchangeRateCoverage } from '@/hooks/useAccountingData'
import { formatDate } from '@/lib/format'

// Item A5. Every dated flow on this app's screens — an income statement,
// a budget's actual spend, the spend curve, a goal's contributions — is
// converted at *its own date's* trailing 30-day rate. The rate cache holds
// two years, so a row older than that has no mean of its own and is
// converted at the oldest one on file instead. That clamp is deliberate
// (the alternatives are a null rate every `sum` silently skips, or a 400
// for the whole page because of one 2019 posting) and it was documented in
// `docs/accounting/currency-handling.md` and nowhere a user could see.
//
// Every other approximation in this app is labelled; this is that label.
// It is a caveat rather than a warning — the figure is not wrong, it is
// approximate — so it takes muted styling rather than the amber a real
// problem gets.
//
// Silent in the two cases where there is nothing to say. `earliest` is
// null when no conversion happens at all (the display currency and every
// currency held are the base currency, so every rate is 1.0 on every day),
// and a window that starts inside the cache contains no clamped row. It
// can still over-warn in one case: a window reaching past the cache that
// happens to hold no non-base row back there. Saying so anyway is the
// honest direction — the alternative is counting clamped rows inside every
// aggregation, which is a much larger change for a strictly weaker signal.
export function ClampedRateNote({ start, displayCurrency }: { start: string; displayCurrency: string }) {
  const { data: coverage } = useExchangeRateCoverage(displayCurrency)
  if (!coverage?.earliest || start >= coverage.earliest) return null

  return (
    <p className="flex items-start gap-2 text-xs text-muted-foreground">
      <Info className="mt-0.5 size-3.5 shrink-0" />
      <span>
        Amounts dated before {formatDate(coverage.earliest)} are converted at the oldest exchange rate on file. The rate
        history covers two years, so anything older uses that rate rather than its own day’s.
      </span>
    </p>
  )
}
