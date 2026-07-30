import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { InlineNameInput } from '@/components/accounting/CategoriesTab'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCreateTag, useDeleteTag, useRenameTag, useTagRenamePreview } from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import type { Tag } from '@/types/accounting'

export function TagsTab({ tags }: { tags: Record<string, Tag> }) {
  const deleteTag = useDeleteTag()
  const createTag = useCreateTag()
  const renamePreview = useTagRenamePreview()
  const renameTagMutation = useRenameTag()
  const [draft, setDraft] = useState('')
  const [newTagError, setNewTagError] = useState<string | null>(null)
  // A merging rename needs the user's confirmation (see
  // `useTagRenamePreview`) before it commits — while that's pending,
  // `renameResetTick` forces the `InlineNameInput` that triggered it to
  // remount (via its `key`), which is what actually reverts its draft text
  // back to the tag's real name on Decline.
  const [pendingRename, setPendingRename] = useState<{ tagId: string; name: string; targetName: string } | null>(null)
  const [renameResetTick, setRenameResetTick] = useState(0)
  const { sorted, sort, toggleSort } = useSortableRows(Object.values(tags), 'name')

  function addTag() {
    if (!draft) return
    createTag.mutate(
      { name: draft },
      {
        onSuccess: () => {
          setDraft('')
          setNewTagError(null)
        },
        onError: () => setNewTagError('This tag already exists'),
      },
    )
  }

  function removeTag(tagId: string) {
    deleteTag.mutate(tagId)
  }

  async function renameTag(tagId: string, name: string) {
    const preview = await renamePreview.mutateAsync({ tagId, name })
    if (preview.will_merge) {
      setPendingRename({ tagId, name, targetName: preview.target_name ?? name })
      return
    }
    renameTagMutation.mutate({ tagId, name })
  }

  function acceptPendingRename() {
    if (!pendingRename) return
    renameTagMutation.mutate({ tagId: pendingRename.tagId, name: pendingRename.name })
    setPendingRename(null)
  }

  function declinePendingRename() {
    setPendingRename(null)
    setRenameResetTick((tick) => tick + 1)
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
                  <TableCell className="font-medium">
                    <InlineNameInput
                      key={`${tag.tag_id}:${renameResetTick}`}
                      value={tag.name}
                      onCommit={(name) => renameTag(tag.tag_id, name)}
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete the tag ${tag.name}`}
                      onClick={() => removeTag(tag.tag_id)}
                    >
                      <Trash2 className="size-3.5 text-muted-foreground" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-0.5">
            <Input
              className="w-48"
              placeholder="e.g. Japan Trip"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                setNewTagError(null)
              }}
              onKeyDown={(event) => event.key === 'Enter' && addTag()}
            />
            {newTagError && <span className="text-xs text-destructive">{newTagError}</span>}
          </div>
          <Button size="sm" onClick={addTag}>
            Add tag
          </Button>
        </div>
      </CardContent>
      {pendingRename && (
        <Dialog open onOpenChange={(open) => !open && declinePendingRename()}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Merge tags?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              Renaming to '{pendingRename.name}' will merge into the existing tag '{pendingRename.targetName}' — this
              can't be undone.
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={declinePendingRename}>
                Decline
              </Button>
              <Button onClick={acceptPendingRename}>Accept</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  )
}
