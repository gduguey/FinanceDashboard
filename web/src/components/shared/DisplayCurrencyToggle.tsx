import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCurrencies } from '@/hooks/useAccountingData'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import type { CurrencyCode } from '@/types/accounting'

// Every account/posting keeps its own native currency (see
// `useDisplayCurrency`'s docstring) — this only picks what an *aggregate*
// total on the current page converts into, placed in each accounting page's
// header per the user's ask. Options come from the backend's supported-
// currency registry, not a hardcoded pair, so a new `CurrencyCode` shows
// up here with no frontend change.
export function DisplayCurrencyToggle() {
  const { displayCurrency, setDisplayCurrency } = useDisplayCurrency()
  const { data: currencies } = useCurrencies()
  const items = Object.fromEntries((currencies ?? []).map((currency) => [currency.code, currency.code]))

  return (
    <Select value={displayCurrency} onValueChange={(value) => value && setDisplayCurrency(value as CurrencyCode)}>
      <SelectTrigger size="sm" className="w-20">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        {(currencies ?? []).map((currency) => (
          <SelectItem key={currency.code} value={currency.code}>
            {currency.code}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
