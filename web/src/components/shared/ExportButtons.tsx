import { Download } from 'lucide-react'
import { Button } from '@/components/ui/button'

// The two-buttons-side-by-side shape every ledger/lots/postings export on
// the dashboard offers — JSON for round-tripping into another tool, CSV
// for opening straight in a spreadsheet. Kept dumb on purpose: callers
// that need a pending/error state (Settings' Export tab) wrap these
// handlers themselves rather than this component knowing about either.
export function ExportButtons({
  onJson,
  onCsv,
}: {
  onJson: () => void | Promise<void>
  onCsv: () => void | Promise<void>
}) {
  return (
    <div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={onJson}>
        <Download className="size-3.5" />
        JSON
      </Button>
      <Button variant="outline" size="sm" onClick={onCsv}>
        <Download className="size-3.5" />
        CSV
      </Button>
    </div>
  )
}
