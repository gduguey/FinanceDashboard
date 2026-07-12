import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { FILTER_ALL } from '@/lib/filters'

export function FilterSelect({
  value,
  exclude,
  items,
  width,
  onValueChange,
  onExcludeChange,
}: {
  value: string
  exclude: boolean
  items: Record<string, string>
  width: string
  onValueChange: (value: string) => void
  onExcludeChange: (exclude: boolean) => void
}) {
  return (
    <div className="flex items-center gap-1">
      <Select value={value} onValueChange={(next) => next && onValueChange(next)}>
        <SelectTrigger size="sm" className={width}>
          <SelectValue items={items} />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([id, name]) => (
            <SelectItem key={id} value={id}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {value !== FILTER_ALL && (
        <Button
          type="button"
          variant={exclude ? 'default' : 'outline'}
          size="sm"
          className="h-8 px-2 text-xs"
          onClick={() => onExcludeChange(!exclude)}
          title={exclude ? 'Excluding this — click to include instead' : 'Including this — click to exclude instead'}
        >
          {exclude ? 'Not' : 'Is'}
        </Button>
      )}
    </div>
  )
}
