import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

// A plain `<input type="number">` bound to `Number(event.target.value) || 0`
// (or `Number.parseFloat(...) || 0`) on every keystroke looks controlled,
// but it isn't really letting you clear the field: deleting "7" down to ""
// immediately re-coerces to 0, which the box then shows as "0" — so typing
// "6" next produces "16", the very digit you thought you'd deleted having
// silently survived as a leading zero. See docs/app-stack/number-input-fix.md
// for the full writeup.
//
// This component avoids that the same way `TaxPanel.tsx`'s `RateField` (the
// pattern this was extracted from) already did: it's *uncontrolled* while
// you're actively typing — `draft` starts `null` ("untouched") and only
// holds a live string once you've typed something — and only parses +
// reports a real number (or `null`, for an intentionally-cleared field) once
// you leave the field. `value` only ever seeds the field's initial display;
// like `RateField`, it does not fight an in-progress edit if the caller's
// own value changes underneath it while you're still typing.
export function NumberInput({
  value,
  onCommit,
  onChange,
  placeholder,
  className,
  disabled,
  min,
  max,
  step,
}: {
  value: number | null
  onCommit: (value: number | null) => void
  /** Fires on every keystroke with the raw, not-yet-committed text — for a caller that needs to react live (a chart preview, a running total) without waiting for blur. */
  onChange?: (raw: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  min?: number
  max?: number
  step?: number
}) {
  const [draft, setDraft] = useState<string | null>(null)

  function commit() {
    if (draft === null) return
    const trimmed = draft.trim()
    const parsed = trimmed === '' ? null : Number(trimmed)
    // Reject non-finite input (e.g. "1e999" → Infinity, which JSON-serializes to
    // null downstream) — treat it as a cleared value rather than committing it.
    onCommit(parsed !== null && Number.isFinite(parsed) ? parsed : null)
    setDraft(null)
  }

  return (
    <Input
      type="number"
      inputMode="decimal"
      className={cn(className)}
      defaultValue={value ?? undefined}
      placeholder={placeholder}
      disabled={disabled}
      min={min}
      max={max}
      step={step}
      onChange={(event) => {
        setDraft(event.target.value)
        onChange?.(event.target.value)
      }}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
    />
  )
}
