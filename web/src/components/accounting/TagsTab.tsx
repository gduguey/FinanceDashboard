import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useSetTags } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import type { Tag } from '@/types/accounting'

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

export function TagsTab({ tags }: { tags: Record<string, Tag> }) {
  const setTags = useSetTags()
  const [draft, setDraft] = useState('')
  const { sorted, sort, toggleSort } = useSortableRows(Object.values(tags), 'name')

  function addTag() {
    if (!draft) return
    const id = `tag:${slugify(draft)}`
    setTags.mutate({ ...tags, [id]: { tag_id: id, name: draft } })
    setDraft('')
  }

  function removeTag(tagId: string) {
    const next = { ...tags }
    delete next[tagId]
    setTags.mutate(next)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tags</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {sorted.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            No tags yet — a tag crosses categories (e.g. a trip involves food, transport, and lodging).
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'name'} desc={sort.desc} onClick={() => toggleSort('name')}>
                  Name
                </SortableTableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((tag) => (
                <TableRow key={tag.tag_id}>
                  <TableCell className="font-medium">{tag.name}</TableCell>
                  <TableCell>
                    <Button variant="ghost" size="icon" onClick={() => removeTag(tag.tag_id)}>
                      <Trash2 className="size-3.5 text-muted-foreground" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex items-end gap-2">
          <Input
            className="w-48"
            placeholder="e.g. Japan Trip"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && addTag()}
          />
          <Button size="sm" onClick={addTag}>
            Add tag
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
