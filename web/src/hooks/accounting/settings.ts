import { useQueries, useQuery } from '@tanstack/react-query'
import { keys } from '@/hooks/accounting/keys'
import { useAccountingMutation } from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import { BASE_CURRENCY } from '@/lib/currency'
import type { CurrencyCode, LlmSettingsUpdate } from '@/types/accounting'

export const useAccountingStore = () => useQuery({ queryKey: keys.store, queryFn: accountingApi.store })

export const useCurrencies = () => useQuery({ queryKey: keys.currencies, queryFn: accountingApi.currencies })

export const useLlmUsage = () => useQuery({ queryKey: keys.llmUsage, queryFn: accountingApi.llmUsage })

export const useLlmSettings = () => useQuery({ queryKey: keys.llmSettings, queryFn: accountingApi.llmSettings })

// `configured` (used to decide whether to re-verify a saved key) lives in
// llm-usage's response rather than llm-settings', which is why the `llm`
// family covers both — saving a brand-new key has to flip `configured` to
// true without waiting for an unrelated refetch to touch that query.
export const useSetLlmSettings = () =>
  useAccountingMutation({
    mutationFn: (update: LlmSettingsUpdate) => accountingApi.setLlmSettings(update),
    changes: ['llm'],
  })

export const useClearLlmSettings = () =>
  useAccountingMutation({ mutationFn: () => accountingApi.clearLlmSettings(), changes: ['llm'] })

export type ConnectionState = 'none' | 'checking' | 'invalid' | 'connected'

/**
 * What to show the user for a request that never reached an answer.
 *
 * @param error - Whatever the query rejected with.
 * @returns A message, never `null` — an errored verify always has something to say.
 */
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Could not reach the provider'
}

// The one shared definition of "is this provider actually connected" —
// Settings and Transactions' usage banner both read this same cached
// query rather than each deciding for themselves. A real auth check (one
// free models.list() call), not just "is a key present" — `configured`
// alone doesn't catch a wrong/expired key. Saving or clearing a key
// invalidates the `llm` family, whose `settings/llm` prefix — via React
// Query's prefix matching — invalidates this query too.
export function useLlmConnectionStatus(provider: 'gemini' | 'mistral'): {
  state: ConnectionState
  error: string | null
} {
  const { data: usage } = useLlmUsage()
  const configured = usage?.[provider]?.configured ?? false
  const verify = useQuery({
    queryKey: keys.llmVerify(provider),
    queryFn: () => accountingApi.verifyLlmSettings(provider),
    enabled: configured,
    staleTime: 30_000,
  })

  if (!configured) return { state: 'none', error: null }
  // A request that failed is an answer, not a pending one. Reading only
  // `isPending`/`data` left this stuck on `checking` forever once the query
  // had exhausted its retries and settled as an error — an offline or 500
  // verify showed a permanent spinner instead of something to act on.
  if (verify.isError) return { state: 'invalid', error: errorMessage(verify.error) }
  if (verify.isPending || !verify.data) return { state: 'checking', error: null }
  if (!verify.data.ok) return { state: 'invalid', error: verify.data.error }
  return { state: 'connected', error: null }
}

export const useSupportedImportKinds = () =>
  useQuery({ queryKey: keys.supportedImportKinds, queryFn: accountingApi.supportedImportKinds })

export const useSyncStatus = () => useQuery({ queryKey: keys.syncStatus, queryFn: accountingApi.syncStatus })

export const useCurrentExchangeRate = (currency: string) =>
  useQuery({
    queryKey: keys.currentExchangeRate(currency),
    queryFn: () => accountingApi.currentExchangeRate(currency),
    retry: false,
  })

export const useExchangeRateHistory = (currency: string) =>
  useQuery({
    queryKey: keys.exchangeRateHistory(currency),
    queryFn: () => accountingApi.exchangeRateHistory(currency),
    retry: false,
  })

// How far back the rate history a figure was converted with actually
// reaches (item A5). `earliest === null` means nothing is converted for
// this user at all — the display currency and every currency they hold
// are the base currency — which is what keeps `ClampedRateNote` silent
// rather than warning about arithmetic that never ran. `retry: false`
// for the same reason the two hooks above use it: an unsynced currency
// answers 400, and retrying a deliberate refusal only delays the render.
export const useExchangeRateCoverage = (displayCurrency: string) =>
  useQuery({
    queryKey: keys.exchangeRateCoverage(displayCurrency),
    queryFn: () => accountingApi.exchangeRateCoverage(displayCurrency),
    retry: false,
  })

// A client-side rates-to-base table, for the one place this app converts
// currencies outside a backend response — mixing accounts and other
// assets into one allocation pie. Every non-base `CurrencyCode` needs its
// own smoothed rate synced first; a currency with none just contributes
// no rate (see `convertCurrency`, which would then leave its amounts
// unconverted rather than throwing mid-render).
export function useRatesToBase(nonBaseCurrencies: CurrencyCode[]) {
  const results = useQueries({
    queries: nonBaseCurrencies.map((code) => ({
      queryKey: keys.currentExchangeRate(code),
      queryFn: () => accountingApi.currentExchangeRate(code),
      retry: false,
    })),
  })
  const ratesToBase: Record<string, number> = { [BASE_CURRENCY]: 1 }
  for (const result of results) {
    if (result.data) ratesToBase[result.data.currency] = result.data.rate_to_base
  }
  return ratesToBase
}
