import type { CurrencyCode } from '@/types/accounting'

// Mirrors `accounting.ledger.currency.convert` — a plain three-way branch
// over the two supported currencies, not a general exchange-rate graph.
export function convertCurrency(amount: number, from: CurrencyCode, to: CurrencyCode, eurUsdRate: number): number {
  if (from === to) return amount
  if (from === 'EUR' && to === 'USD') return amount * eurUsdRate
  return amount / eurUsdRate
}
