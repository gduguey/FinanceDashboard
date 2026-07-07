import { ArchiveRestore } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useDismissedSuggestions, useRestoreSuggestion } from '@/hooks/useAccountingData'
import type { DismissedSuggestionKind } from '@/types/accounting'

// Dismissing a suggestion never touches a rule, a posting, or a merge —
// it only stops that one pair/group from being proposed again, so nothing
// here is destructive. Shown collapsed-by-default-empty (renders nothing
// until something's actually been dismissed) rather than an always-visible
// empty state, since an archive nobody's used yet isn't worth a permanent
// spot on the page.
export function SuggestionArchive({ kind }: { kind: DismissedSuggestionKind }) {
  const { data } = useDismissedSuggestions()
  const restore = useRestoreSuggestion()
  const entries = (data ?? []).filter((entry) => entry.kind === kind)

  if (entries.length === 0) return null

  return (
    <div className="space-y-2 rounded-md border border-dashed border-border p-3">
      <p className="text-xs font-medium text-muted-foreground">Dismissed — not shown as suggestions ({entries.length})</p>
      <ul className="space-y-1">
        {entries.map((entry) => (
          <li key={entry.suggestion_id} className="flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate text-muted-foreground" title={entry.description}>
              {entry.description}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs"
              disabled={restore.isPending}
              onClick={() => restore.mutate(entry.suggestion_id)}
            >
              <ArchiveRestore className="size-3.5" />
              Restore
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
