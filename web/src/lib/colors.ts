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

// A large, deterministic set of visually distinct colors for categories and
// subcategories — mirrors `accounting.taxonomy.CATEGORY_COLOR_PALETTE` on the
// backend (same named colors first, then the same golden-angle HSL
// stepping), so a category created from this UI never repeats a color
// already in use and never lands on white (the app's own background color).
const NAMED_CATEGORY_COLORS = [
  '#ef4444', // red
  '#f97316', // orange
  '#f59e0b', // amber
  '#eab308', // yellow
  '#84cc16', // lime
  '#22c55e', // green
  '#10b981', // emerald
  '#14b8a6', // teal
  '#06b6d4', // cyan
  '#0ea5e9', // sky
  '#3b82f6', // blue
  '#6366f1', // indigo
  '#8b5cf6', // violet
  '#a855f7', // purple
  '#d946ef', // fuchsia
  '#ec4899', // pink
  '#f43f5e', // rose
]

function hslToHex(hueDeg: number, saturation: number, lightness: number): string {
  const hue = ((hueDeg % 360) + 360) % 360
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation
  const huePrime = hue / 60
  const x = chroma * (1 - Math.abs((huePrime % 2) - 1))
  const [r1, g1, b1] =
    huePrime < 1
      ? [chroma, x, 0]
      : huePrime < 2
        ? [x, chroma, 0]
        : huePrime < 3
          ? [0, chroma, x]
          : huePrime < 4
            ? [0, x, chroma]
            : huePrime < 5
              ? [x, 0, chroma]
              : [chroma, 0, x]
  const m = lightness - chroma / 2
  const toHex = (channel: number) =>
    Math.round((channel + m) * 255)
      .toString(16)
      .padStart(2, '0')
  return `#${toHex(r1)}${toHex(g1)}${toHex(b1)}`
}

function buildCategoryColorPalette(count: number): string[] {
  const palette = [...NAMED_CATEGORY_COLORS]
  const seen = new Set(palette.map((color) => color.toLowerCase()))
  const lightnessBands = [0.5, 0.35, 0.62]
  let hue = 0
  let band = 0
  while (palette.length < count) {
    hue = (hue + 137.508) % 360
    const color = hslToHex(hue, 0.6, lightnessBands[band % lightnessBands.length])
    band += 1
    if (seen.has(color.toLowerCase())) continue
    seen.add(color.toLowerCase())
    palette.push(color)
  }
  return palette
}

export const CATEGORY_COLOR_PALETTE = buildCategoryColorPalette(1000)

// Picks the first palette color not already assigned to a category —
// callers pass every color already in use (plus anything freshly assigned
// earlier in the same batch) so two categories/subcategories created
// together never collide either.
export function nextAvailableColor(usedColors: Iterable<string>): string {
  const used = new Set([...usedColors].map((color) => color.toLowerCase()))
  return (
    CATEGORY_COLOR_PALETTE.find((color) => !used.has(color.toLowerCase())) ??
    CATEGORY_COLOR_PALETTE[CATEGORY_COLOR_PALETTE.length - 1]
  )
}

// Appends an alpha channel to a `#rrggbb` color — `alpha` is 0 (fully
// transparent) to 1 (opaque) — for a subtle tinted background that still
// lets the row's own background (and dark/light theme) show through.
export function withAlpha(hex: string, alpha: number): string {
  const alphaHex = Math.round(alpha * 255)
    .toString(16)
    .padStart(2, '0')
  return `${hex}${alphaHex}`
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
