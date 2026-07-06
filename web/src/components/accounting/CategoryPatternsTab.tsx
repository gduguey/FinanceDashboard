import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useSetCategoryPatterns } from '@/hooks/useAccountingData'
import type { Category, CategoryPattern } from '@/types/accounting'

function categoryName(categories: Record<string, Category>, categoryId: string) {
  return categories[categoryId]?.name ?? categoryId
}

function PatternEditDialog({
  pattern,
  categories,
  onClose,
  onSave,
}: {
  pattern: CategoryPattern
  categories: Record<string, Category>
  onClose: () => void
  onSave: (pattern: CategoryPattern) => void
}) {
  const [draft, setDraft] = useState(pattern)
  const classification = categories[draft.category_id]?.classification ?? 'expense'
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit category pattern</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            If description contains
            <Input
              value={draft.description_contains}
              onChange={(event) => setDraft((prev) => ({ ...prev, description_contains: event.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Suggested category
            <CategorySelect
              categories={categories}
              classification={classification}
              value={draft.category_id}
              onChange={(categoryId) => setDraft((prev) => ({ ...prev, category_id: categoryId ?? prev.category_id, subcategory_id: null }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Suggested subcategory
            <SubcategorySelect
              categories={categories}
              categoryId={draft.category_id}
              value={draft.subcategory_id}
              onChange={(subcategoryId) => setDraft((prev) => ({ ...prev, subcategory_id: subcategoryId }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Priority (lower wins ties)
            <Input
              type="number"
              value={draft.priority}
              onChange={(event) => setDraft((prev) => ({ ...prev, priority: Number(event.target.value) }))}
            />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onSave(draft)
              onClose()
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// A description-match pattern that only ever *suggests* a category (see
// `pending_source: "pattern"` on postings) — distinct from `Rule`, which
// resolves a posting's counterparty/category automatically with no
// confirmation step. A suggestion from here always needs the same
// accept/reject pass in Transactions that an AI suggestion does.
export function CategoryPatternsTab({
  patterns,
  categories,
}: {
  patterns: Record<string, CategoryPattern>
  categories: Record<string, Category>
}) {
  const setPatterns = useSetCategoryPatterns()
  const [editing, setEditing] = useState<CategoryPattern | null>(null)
  const [draft, setDraft] = useState<{ descriptionContains: string; categoryId: string | null }>({
    descriptionContains: '',
    categoryId: null,
  })
  const patternList = Object.values(patterns)
  const { sorted, sort, toggleSort } = useSortableRows(patternList, 'priority')

  function addPattern() {
    if (!draft.descriptionContains || !draft.categoryId) return
    const patternId = `pattern:${Date.now()}`
    const pattern: CategoryPattern = {
      pattern_id: patternId,
      description_contains: draft.descriptionContains,
      category_id: draft.categoryId,
      subcategory_id: null,
      priority: 100,
    }
    setPatterns.mutate({ ...patterns, [patternId]: pattern })
    setDraft({ descriptionContains: '', categoryId: null })
  }

  function removePattern(patternId: string) {
    const { [patternId]: _removed, ...rest } = patterns
    setPatterns.mutate(rest)
  }

  function savePattern(updated: CategoryPattern) {
    setPatterns.mutate({ ...patterns, [updated.pattern_id]: updated })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Category patterns</CardTitle>
        <CardDescription>
          Unlike Rules (which resolve a posting's category automatically, with no confirmation step), a pattern only
          ever proposes a category — it lands as a temporary suggestion in Transactions, in blue, that still needs to
          be validated before it sticks.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader>
            <TableRow>
              <SortableTableHead
                active={sort.key === 'description_contains'}
                desc={sort.desc}
                onClick={() => toggleSort('description_contains')}
              >
                If description contains
              </SortableTableHead>
              <TableHead>Suggests</TableHead>
              <SortableTableHead active={sort.key === 'priority'} desc={sort.desc} onClick={() => toggleSort('priority')}>
                Priority
              </SortableTableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((pattern) => (
              <TableRow key={pattern.pattern_id}>
                <TableCell className="font-medium">{pattern.description_contains}</TableCell>
                <TableCell className="text-muted-foreground">
                  {categoryName(categories, pattern.category_id)}
                  {pattern.subcategory_id ? ` › ${categoryName(categories, pattern.subcategory_id)}` : ''}
                </TableCell>
                <TableCell className="text-muted-foreground">{pattern.priority}</TableCell>
                <TableCell className="flex gap-1">
                  <Button variant="ghost" size="icon" onClick={() => setEditing(pattern)}>
                    <Pencil className="size-3.5 text-muted-foreground" />
                  </Button>
                  <Button variant="ghost" size="icon" onClick={() => removePattern(pattern.pattern_id)}>
                    <Trash2 className="size-3.5 text-muted-foreground" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Description contains
            <Input
              className="w-56"
              value={draft.descriptionContains}
              onChange={(event) => setDraft((prev) => ({ ...prev, descriptionContains: event.target.value }))}
              placeholder="e.g. TRADER JOE"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Suggested category
            <CategorySelect
              categories={categories}
              classification="expense"
              value={draft.categoryId}
              onChange={(categoryId) => setDraft((prev) => ({ ...prev, categoryId }))}
            />
          </label>
          <Button size="sm" onClick={addPattern}>
            Add pattern
          </Button>
        </div>
      </CardContent>
      {editing && (
        <PatternEditDialog pattern={editing} categories={categories} onClose={() => setEditing(null)} onSave={savePattern} />
      )}
    </Card>
  )
}
