import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

export function MultiSelectFilter({
  label,
  options,
  selected,
  onChange,
}: {
  label: string
  options: { id: string; name: string }[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((existing) => existing !== id) : [...selected, id])
  }

  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" />}>
        {label}
        {selected.length > 0 && (
          <Badge variant="secondary" className="h-4 px-1.5">
            {selected.length}
          </Badge>
        )}
      </PopoverTrigger>
      <PopoverContent className="w-56 p-2">
        <div className="flex items-center justify-between px-1 pb-1.5">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          {selected.length > 0 && (
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => onChange([])}
            >
              Clear
            </button>
          )}
        </div>
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
