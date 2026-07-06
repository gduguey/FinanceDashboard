import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

// A date filter that can be entirely unset (no constraint applied) is
// easy to misread once rendered as a native `<input type="date">` —
// depending on the browser, an empty one can look enough like "today" is
// already entered that the filter reads as actively narrowed to today
// when nothing is actually being enforced. This makes the two states
// visually unmistakable instead: unset shows a plain ghost button (never
// a date-shaped box at all), and a real date box — with an explicit
// clear (×) — only ever appears once a value has actually been chosen.
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
  const [editing, setEditing] = useState(false)

  if (!value && !editing) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={`${className} justify-start font-normal text-muted-foreground`}
        onClick={() => setEditing(true)}
      >
        {placeholder}
      </Button>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <Input
        type="date"
        className={className}
        value={value}
        autoFocus={editing && !value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => {
          if (!value) setEditing(false)
        }}
      />
      {value && (
        <button
          type="button"
          onClick={() => {
            onChange('')
            setEditing(false)
          }}
          className="text-muted-foreground hover:text-foreground"
          title="Clear date"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}
