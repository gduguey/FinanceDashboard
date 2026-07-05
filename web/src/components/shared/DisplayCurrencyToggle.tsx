import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useDisplayCurrency } from '@/hooks/useDisplayCurrency'
import type { CurrencyCode } from '@/types/accounting'

// Every account/posting keeps its own native currency (see
// `useDisplayCurrency`'s docstring) — this only picks what an *aggregate*
// total on the current page converts into, placed in each accounting page's
// header per the user's ask.
export function DisplayCurrencyToggle() {
  const { displayCurrency, setDisplayCurrency } = useDisplayCurrency()
  return (
    <Select value={displayCurrency} onValueChange={(value) => value && setDisplayCurrency(value as CurrencyCode)}>
      <SelectTrigger size="sm" className="w-20">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="USD">USD</SelectItem>
        <SelectItem value="EUR">EUR</SelectItem>
      </SelectContent>
    </Select>
  )
}
