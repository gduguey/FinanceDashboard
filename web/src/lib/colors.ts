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

// Lightens a hex color toward white by `amount` (0 = unchanged, 1 = white) —
// used to give a subcategory a shade of its parent category's color rather
// than an unrelated palette color, so a chart broken down by both reads as
// one color family per category.
export function lighten(hex: string, amount: number): string {
  const match = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
  if (!match) return hex
  const [r, g, b] = match.slice(1).map((channel) => Number.parseInt(channel, 16))
  const blend = (channel: number) => Math.round(channel + (255 - channel) * amount)
  return `#${[blend(r), blend(g), blend(b)].map((channel) => channel.toString(16).padStart(2, '0')).join('')}`
}
