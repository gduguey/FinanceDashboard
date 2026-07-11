// Turns an already-fetched JS value into a downloadable file — the
// backend export endpoints just return plain JSON like any other GET, so
// giving the user a real `.json`/`.csv` file (rather than a browser tab
// full of raw text) is entirely a frontend concern. `triggerDownload` is
// the shared plumbing; `downloadJson`/`downloadCsv` are the two shapes
// every export on this dashboard offers.
function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function downloadJson(data: unknown, filename: string): void {
  triggerDownload(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), filename)
}

// A cell that's itself an object or array (`meta`, `tag_ids`) has no
// native CSV representation — inlined as JSON text instead of dropped,
// so the CSV stays a lossless (if slightly awkward) view of the same
// record the JSON export has, rather than a quietly incomplete one.
function csvCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

// Column set is the union of every row's own keys, not just the first
// row's — a field that's `null`/absent on an early row but present later
// (e.g. a posting with no `subcategory_id` followed by one that has it)
// must still get its own column, not be silently dropped. Takes any
// record-shaped row (a specific interface like `LedgerEvent`, not just a
// bare `Record<string, unknown>`) — every export type on this dashboard
// is a fixed interface, never an actual index-signature record.
function toCsv(rows: object[]): string {
  if (rows.length === 0) return ''
  const records = rows as Record<string, unknown>[]
  const headers = [...new Set(records.flatMap((row) => Object.keys(row)))]
  const lines = [headers.join(','), ...records.map((row) => headers.map((header) => csvCell(row[header])).join(','))]
  return lines.join('\n')
}

export function downloadCsv(rows: object[], filename: string): void {
  triggerDownload(new Blob([toCsv(rows)], { type: 'text/csv' }), filename)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Firing several `downloadCsv` calls back to back in the same tick (e.g.
// Settings' "Lots" export, which is really three files: open lots, closed
// lots, symbol rollup) isn't reliable — browsers treat a burst of
// programmatic downloads from one click as suspicious and silently drop all
// but one of them, with no error either side can catch. Spacing each one out
// gives the browser room to actually start the previous download before the
// next one fires.
export async function downloadMultipleCsv(files: { rows: object[]; filename: string }[]): Promise<void> {
  for (const [index, file] of files.entries()) {
    if (index > 0) await sleep(300)
    downloadCsv(file.rows, file.filename)
  }
}

// For an endpoint that already returns a file (a `.zip` with its own
// `Content-Disposition: attachment` header, e.g. the raw-statements
// exports) — no fetch/blob needed, the browser handles the download
// itself once navigated to the URL.
export function downloadFromUrl(url: string): void {
  const link = document.createElement('a')
  link.href = url
  link.click()
}

// A collision-resistant suffix for export filenames — includes time down
// to the second, not just the date, so exporting the same thing twice in
// one day never produces the exact same filename. Browsers auto-rename a
// same-name collision on their own ("(1)", "(2)", …), but that's the
// browser's call to make, not something to force by giving it identical
// names to begin with.
export function exportStamp(): string {
  return new Date().toISOString().replace(/:/g, '-').split('.')[0]
}
