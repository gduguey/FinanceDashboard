import type { CurrencyCode } from '@/types/accounting'

// Mirrors `accounting.ledger.currency.convert` — every rate is expressed
// as "how many BASE_CURRENCY units one unit of this currency is worth"
// (BASE_CURRENCY itself always maps to 1), so converting between any two
// supported currencies is always a trip through that shared base, never a
// hardcoded pair. Adding a currency to `CurrencyCode` needs no change here.
export function convertCurrency(
  amount: number,
  from: CurrencyCode,
  to: CurrencyCode,
  ratesToBase: Record<string, number>,
): number {
  if (from === to) return amount
  const amountInBase = amount * ratesToBase[from]
  return amountInBase / ratesToBase[to]
}
