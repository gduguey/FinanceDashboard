import { SlidersHorizontal } from 'lucide-react'
import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// The trigger + count badge + scrollable popover shell every "many filters"
// page shares — the page itself only supplies the filter rows (each
// typically a small label above a FilterSelect/MultiSelectFilter/etc.),
// not the open/close or overflow behavior.
export function FilterPanel({ activeCount, children }: { activeCount: number; children: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" />}>
        <SlidersHorizontal className="size-3.5" />
        Filters
        {activeCount > 0 && (
          <Badge variant="secondary" className="h-4 px-1.5">
            {activeCount}
          </Badge>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="max-h-[70vh] w-80 space-y-3 overflow-y-auto">
        {children}
      </PopoverContent>
    </Popover>
  )
}

// One labeled row inside a FilterPanel — every filter control gets its own
// small heading since, unlike the old inline row, a vertical panel has no
// other way to say which control is which.
export function FilterRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}
