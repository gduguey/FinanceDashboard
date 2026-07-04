import type { BenchmarkSetting, HysaRates, HysaSettings } from '@/types/portfolio'

// Resolves what the user actually selected — never a bare "Benchmark" or
// "HYSA" label that leaves you guessing which symbol or bank the chart is
// showing.
export function benchmarkLabel(setting: BenchmarkSetting | undefined): string {
  return setting?.symbol_override || setting?.default_symbol || 'benchmark'
}

export function hysaLabel(settings: HysaSettings | undefined, rates: HysaRates | undefined): string {
  if (settings?.fixed_rate_pct != null) return `Custom, ${settings.fixed_rate_pct}%`
  const bankId = settings?.bank_id || rates?.default_bank_id
  const bank = rates?.banks.find((b) => b.bank_id === bankId)
  return bank?.bank_name ?? 'HYSA'
}
