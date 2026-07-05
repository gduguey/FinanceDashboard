import { useEffect, useState } from 'react'
import type { CurrencyCode } from '@/types/accounting'

const STORAGE_KEY = 'accounting.display-currency'

function readStored(): CurrencyCode {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'EUR' ? 'EUR' : 'USD'
}

// Every account/posting/other-asset keeps its own native currency at rest
// (see `accounting.models.CurrencyCode`) — this only controls which
// currency a page's *aggregate* totals are converted into, shared across
// tabs via localStorage so switching pages doesn't reset it.
export function useDisplayCurrency() {
  const [displayCurrency, setDisplayCurrencyState] = useState<CurrencyCode>(readStored)

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === STORAGE_KEY) setDisplayCurrencyState(readStored())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  function setDisplayCurrency(next: CurrencyCode) {
    localStorage.setItem(STORAGE_KEY, next)
    setDisplayCurrencyState(next)
  }

  return { displayCurrency, setDisplayCurrency }
}
