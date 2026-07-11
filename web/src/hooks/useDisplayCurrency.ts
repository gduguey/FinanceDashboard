import { usePersistedState } from '@/hooks/usePersistedState'
import type { CurrencyCode } from '@/types/accounting'

const STORAGE_KEY = 'accounting.display-currency'

// Every account/posting/other-asset keeps its own native currency at rest
// (see `accounting.models.CurrencyCode`) — this only controls which
// currency a page's *aggregate* totals are converted into. Persisted and
// reactive across every component reading it (see `usePersistedState`), so
// the header toggle updates every chart on the page immediately, and the
// choice survives navigating to another page.
export function useDisplayCurrency() {
  const [displayCurrency, setDisplayCurrency] = usePersistedState<CurrencyCode>(STORAGE_KEY, 'USD')
  return { displayCurrency, setDisplayCurrency }
}
