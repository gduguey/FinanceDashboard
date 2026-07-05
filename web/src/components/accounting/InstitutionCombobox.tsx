import { useState } from 'react'
import { Input } from '@/components/ui/input'

// A lightweight type-to-search-or-add combobox — no floating-panel
// primitive exists yet in this codebase, so this renders its suggestion
// list as a plain absolutely-positioned panel under the input rather than
// pulling in a new Popover dependency for one field.
export function InstitutionCombobox({
  value,
  onChange,
  knownInstitutions,
}: {
  value: string
  onChange: (value: string) => void
  knownInstitutions: string[]
}) {
  const [isOpen, setIsOpen] = useState(false)
  const matches = knownInstitutions.filter((institution) => institution.toLowerCase().includes(value.toLowerCase()))

  return (
    <div className="relative">
      <Input
        className="w-40 text-foreground"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        placeholder="e.g. Chase"
      />
      {isOpen && value && matches.length > 0 && (
        <ul className="absolute z-20 mt-1 w-48 rounded-md border border-border bg-white py-1 text-sm shadow-md">
          {matches.map((institution) => (
            <li key={institution}>
              <button
                type="button"
                className="block w-full px-3 py-1.5 text-left text-foreground hover:bg-muted"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onChange(institution)}
              >
                {institution}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
