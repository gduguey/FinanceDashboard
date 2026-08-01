import { Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CardTitle } from '@/components/ui/card'

// The left-hand side of the table's header: what the table is showing, how
// many rows that is, and the three bulk actions scoped to the current filter.
// Each button is present only when it has something to act on.
//
// Every number here counts what the *filter* matches, never what the page
// happens to be rendering, and each is the server's own count. That is the
// point rather than a detail: all three actions resolve their target set from
// the filter server-side, so a label counting the rows on screen would report
// a number that is not the number the click affects.
export function TransactionsToolbar({
  onlyUncategorized,
  matchedPostings,
  bulkTargetCount,
  pendingCount,
  pendingSelectedCount,
  bulkProgress,
  bulkSuggesting,
  bulkPatternSuggesting,
  aiAvailable,
  validatePendingIsPending,
  onBulkAiSuggest,
  onBulkPatternSuggest,
  onValidatePending,
}: {
  onlyUncategorized: boolean
  matchedPostings: number
  bulkTargetCount: number
  pendingCount: number
  pendingSelectedCount: number
  bulkProgress: { done: number; total: number } | null
  bulkSuggesting: boolean
  bulkPatternSuggesting: boolean
  aiAvailable: boolean
  validatePendingIsPending: boolean
  onBulkAiSuggest: () => void
  onBulkPatternSuggest: () => void
  onValidatePending: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <CardTitle>{onlyUncategorized ? 'Needs categorizing' : 'All transactions'}</CardTitle>
      {/* Rows, not transactions. A split transaction is one transaction and
          several rows, and this table lists rows — the label used to say
          "transactions" and was wrong in exactly that case. See
          `api_models.PostingPageCounts`. */}
      <span className="text-xs text-muted-foreground">
        {matchedPostings.toLocaleString()} row{matchedPostings === 1 ? '' : 's'}
      </span>
      {bulkTargetCount > 0 && (
        <Button
          variant="outline"
          size="sm"
          disabled={bulkSuggesting || !aiAvailable}
          title={aiAvailable ? undefined : 'No AI provider configured — add a key in Settings'}
          onClick={onBulkAiSuggest}
        >
          <Sparkles className="size-3.5" />
          {bulkProgress
            ? `Suggesting ${bulkProgress.done}/${bulkProgress.total}…`
            : `AI suggest all (${bulkTargetCount.toLocaleString()})`}
        </Button>
      )}
      {bulkTargetCount > 0 && (
        <Button variant="outline" size="sm" disabled={bulkPatternSuggesting} onClick={onBulkPatternSuggest}>
          <Sparkles className="size-3.5" />
          {bulkPatternSuggesting
            ? 'Matching patterns…'
            : `Run pattern suggestions (${bulkTargetCount.toLocaleString()})`}
        </Button>
      )}
      {pendingCount > 0 && (
        <Button
          variant="outline"
          size="sm"
          disabled={validatePendingIsPending}
          title="Accepts every checked suggestion the current filter matches and reverts every unchecked one — across every page, not just this one."
          onClick={onValidatePending}
        >
          Validate matching ({pendingSelectedCount.toLocaleString()}/{pendingCount.toLocaleString()})
        </Button>
      )}
    </div>
  )
}
