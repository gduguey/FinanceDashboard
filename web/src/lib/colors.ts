// A small, muted categorical palette — reused by every chart that breaks a
// series down by symbol, so a given symbol reads as the same color everywhere.
export const SYMBOL_PALETTE = [
  '#0f172a', // slate-900
  '#2563eb', // blue-600
  '#059669', // emerald-600
  '#d97706', // amber-600
  '#dc2626', // red-600
  '#7c3aed', // violet-600
  '#0891b2', // cyan-600
  '#be185d', // pink-700
]

export function colorForIndex(index: number): string {
  return SYMBOL_PALETTE[index % SYMBOL_PALETTE.length]
}
