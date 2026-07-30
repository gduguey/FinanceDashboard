import { Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import type { Tag } from '@/types/accounting'

export function TagsCell({
  tagIds,
  tags,
  onChange,
}: {
  tagIds: string[]
  tags: Record<string, Tag>
  onChange: (tagIds: string[]) => void
}) {
  const available = Object.values(tags)
    .filter((tag) => !tagIds.includes(tag.tag_id))
    .sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div className="flex flex-wrap items-center gap-1">
      {tagIds.map((tagId) => (
        <Badge key={tagId} variant="outline" className="gap-1 pr-1">
          {tags[tagId]?.name ?? tagId}
          <button
            type="button"
            onClick={() => onChange(tagIds.filter((id) => id !== tagId))}
            className="text-muted-foreground/60 hover:text-destructive"
          >
            <X className="size-2.5" />
          </button>
        </Badge>
      ))}
      {available.length > 0 && (
        <Select value="" onValueChange={(next) => next && onChange([...tagIds, next])}>
          <SelectTrigger
            size="sm"
            className="h-6 w-6 justify-center border-none p-0 shadow-none [&>svg]:hidden"
            aria-label="Add tag"
          >
            <Plus className="size-3.5 text-muted-foreground/60 hover:text-foreground" />
          </SelectTrigger>
          <SelectContent>
            {available.map((tag) => (
              <SelectItem key={tag.tag_id} value={tag.tag_id}>
                {tag.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  )
}
