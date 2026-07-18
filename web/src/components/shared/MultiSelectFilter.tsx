import { ChevronDownIcon } from 'lucide-react'
import { IncludeExcludeToggle } from '@/components/shared/IncludeExcludeToggle'
import { Badge } from '@/components/ui/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// Deliberately styled to match `SelectTrigger` (ui/select.tsx) almost to the
// class, not a generic `<Button>` — same height/border/background/text size,
// same trailing chevron — so a multi-select filter reads as "the same kind
// of control" as every single-select one next to it in a filter panel,
// rather than looking like an unrelated pill button that happens to live
// in the same row.
export function MultiSelectFilter({
  label,
  options,
  selected,
  exclude,
  onSelectedChange,
  onExcludeChange,
}: {
  label: string
  options: { id: string; name: string }[]
  selected: string[]
  exclude: boolean
  onSelectedChange: (next: string[]) => void
  onExcludeChange: (exclude: boolean) => void
}) {
  function toggle(id: string) {
    onSelectedChange(selected.includes(id) ? selected.filter((existing) => existing !== id) : [...selected, id])
  }

  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            className="flex h-7 w-full items-center justify-between gap-1.5 rounded-[min(var(--radius-md),10px)] border border-input bg-transparent py-1 pr-2 pl-2.5 text-sm text-foreground outline-none select-none transition-colors hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 data-popup-open:bg-muted"
          />
        }
      >
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left">
          {exclude && selected.length > 0 ? 'Not ' : ''}
          {label}
          {selected.length > 0 && (
            <Badge variant="secondary" className="h-4 px-1.5">
              {selected.length}
            </Badge>
          )}
        </span>
        <ChevronDownIcon className="pointer-events-none size-4 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent className="w-56 p-2">
        <div className="flex items-center justify-between gap-2 px-1 pb-1.5">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          {selected.length > 0 && (
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => onSelectedChange([])}
            >
              Clear
            </button>
          )}
        </div>
        {selected.length > 0 && (
          <div className="px-1 pb-1.5">
            <IncludeExcludeToggle exclude={exclude} onChange={onExcludeChange} />
          </div>
        )}
        <div className="max-h-64 space-y-0.5 overflow-y-auto">
          {options.map((option) => (
            <label key={option.id} className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted">
              <input
                type="checkbox"
                className="size-3.5 accent-current"
                checked={selected.includes(option.id)}
                onChange={() => toggle(option.id)}
              />
              {option.name}
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
