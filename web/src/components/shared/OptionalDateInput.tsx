import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'

// A date filter that can be entirely unset (no constraint applied) is
// easy to misread once rendered as a native `<input type="date">` —
// depending on the browser, an empty one can render its own placeholder
// in a way that's easily mistaken for "today" already being entered, so
// the filter reads as actively narrowed to today when nothing is being
// enforced. The native input is always here — never swapped out for a
// separate "reveal" control, which would cost an extra click just to
// start picking a date — but its own empty-state rendering is hidden
// (`text-transparent`) in favor of this component's own unambiguous
// placeholder text, and a value only ever shows an explicit clear (×).
export function OptionalDateInput({
  value,
  onChange,
  placeholder = 'Any date',
  className = 'w-36',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <div className={`flex items-center gap-1 ${className}`}>
      <div className="relative flex-1">
        <Input
          type="date"
          className={value ? 'w-full' : 'w-full text-transparent'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        {!value && (
          <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-sm text-muted-foreground">
            {placeholder}
          </span>
        )}
      </div>
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="text-muted-foreground hover:text-foreground"
          title="Clear date"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}
