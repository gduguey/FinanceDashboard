import { cn } from '@/lib/utils'

// A single button that flips its own label ("Is" ↔ "Not") to show the
// current mode makes you read the label to find out which mode you're
// already in, before you can tell whether clicking helps or hurts. A
// segmented control shows both options at once with the active one
// highlighted — the current state and the two choices are both visible
// in the same glance, the same reason a light switch beats a single
// unlabeled push-button toggle.
export function IncludeExcludeToggle({
  exclude,
  onChange,
}: {
  exclude: boolean
  onChange: (exclude: boolean) => void
}) {
  return (
    <div className="inline-flex shrink-0 rounded-md border border-input p-0.5 text-xs">
      <button
        type="button"
        className={cn(
          'rounded-[5px] px-2 py-1 font-medium transition-colors',
          !exclude ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
        )}
        aria-pressed={!exclude}
        onClick={() => onChange(false)}
      >
        Is
      </button>
      <button
        type="button"
        className={cn(
          'rounded-[5px] px-2 py-1 font-medium transition-colors',
          exclude ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
        )}
        aria-pressed={exclude}
        onClick={() => onChange(true)}
      >
        Is not
      </button>
    </div>
  )
}
