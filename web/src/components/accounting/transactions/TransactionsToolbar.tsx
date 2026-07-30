import { Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CardTitle } from '@/components/ui/card'

// The left-hand side of the table's header: what the table is showing, how
// many rows that is, and the three bulk actions scoped to the current filter.
// Each button is present only when it has something to act on.
export function TransactionsToolbar({
  onlyUncategorized,
  visibleCount,
  bulkTargetCount,
  bulkProgress,
  bulkSuggesting,
  bulkPatternSuggesting,
  aiAvailable,
  pendingInViewCount,
  checkedPendingCount,
  validatePendingIsPending,
  onBulkAiSuggest,
  onBulkPatternSuggest,
  onValidateSelection,
}: {
  onlyUncategorized: boolean
  visibleCount: number
  bulkTargetCount: number
  bulkProgress: { done: number; total: number } | null
  bulkSuggesting: boolean
  bulkPatternSuggesting: boolean
  aiAvailable: boolean
  pendingInViewCount: number
  checkedPendingCount: number
  validatePendingIsPending: boolean
  onBulkAiSuggest: () => void
  onBulkPatternSuggest: () => void
  onValidateSelection: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <CardTitle>{onlyUncategorized ? 'Needs categorizing' : 'All transactions'}</CardTitle>
      <span className="text-xs text-muted-foreground">
        {visibleCount.toLocaleString()} transaction{visibleCount === 1 ? '' : 's'}
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
            : `AI suggest all (${bulkTargetCount})`}
        </Button>
      )}
      {bulkTargetCount > 0 && (
        <Button variant="outline" size="sm" disabled={bulkPatternSuggesting} onClick={onBulkPatternSuggest}>
          <Sparkles className="size-3.5" />
          {bulkPatternSuggesting ? 'Matching patterns…' : `Run pattern suggestions (${bulkTargetCount})`}
        </Button>
      )}
      {pendingInViewCount > 0 && (
        <Button variant="outline" size="sm" disabled={validatePendingIsPending} onClick={onValidateSelection}>
          Validate selection ({checkedPendingCount}/{pendingInViewCount})
        </Button>
      )}
    </div>
  )
}
