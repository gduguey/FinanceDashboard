import { Pencil, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useCategoryDeletePreview,
  useCategoryRenamePreview,
  useCreateCategory,
  useCreateCategoryPattern,
  useCreateSubcategory,
  useDeleteCategory,
  useDeleteCategoryPattern,
  usePatchCategoryPattern,
  useRenameCategory,
} from '@/hooks/useAccountingData'
import { useSortableRows } from '@/hooks/useSortableRows'
import type { BudgetToDeletePreview } from '@/lib/accountingApi'
import { nextAvailableColor } from '@/lib/colors'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Category, CategoryClassification, CategoryPattern } from '@/types/accounting'

// An inline click-to-rename field — looks like plain text until focused, at
// which point it grows to fit what's typed (`field-sizing-content`) rather
// than reflowing the row around a fixed-width box. Commits on blur/Enter,
// reverts on Escape or an empty result (a category always needs a name).
// Exported so `TagsTab` can reuse the exact same rename affordance.
export function InlineNameInput({
  value,
  onCommit,
  className,
}: {
  value: string
  onCommit: (name: string) => void
  className?: string
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  function commit() {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== value) onCommit(trimmed)
    else setDraft(value)
  }

  return (
    <Input
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
        if (event.key === 'Escape') {
          setDraft(value)
          event.currentTarget.blur()
        }
      }}
      className={cn(
        'h-auto w-auto field-sizing-content rounded border border-transparent bg-transparent px-1 py-0 hover:border-border focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/50',
        className,
      )}
    />
  )
}

// The taxonomy every fresh install is seeded with (see
// `accounting.store._EXPENSE_TAXONOMY`/`_INCOME_TAXONOMY`) — shown here
// purely as reference, since it's just a starting point: renaming,
// merging, or deleting any of it above is completely safe once seeded.
const DEFAULT_EXPENSE_TAXONOMY: [string, string[]][] = [
  ['Food & Drink', ['Groceries', 'Restaurants & Takeout', 'Coffee & Snacks', 'Delivery']],
  ['Going Out & Entertainment', ['Bars & Alcohol', 'Activities', 'Events']],
  ['Transport', ['Public Transit', 'Rideshare', 'Gas', 'Parking & Tolls', 'Car Rental', 'Bike']],
  ['Home & Housing', ['Rent', 'Utilities', 'Furnishing & Move-in', 'Household Supplies']],
  ['Health', ['Insurance', 'Medical & Pharmacy', 'Fitness']],
  ['Shopping', ['Clothing', 'Electronics', 'Personal Care', 'Gifts Given', 'Hobbies & Recreation']],
  ['Subscriptions', ['Phone & Internet', 'Software', 'Streaming']],
  ['Travel', ['Flights', 'Lodging', 'Activities']],
  ['Admin & Fees', ['Bank Fees', 'Visa & Immigration', 'Taxes', 'Legal & Equity', 'Shipping & Postal']],
]

const DEFAULT_INCOME_TAXONOMY: [string, string[]][] = [
  ['Salary', []],
  ['Bonus', []],
  ['Reimbursement', ['Employer', 'Friend Repayment']],
  ['Gift Received', []],
  ['Tax Refund', []],
  ['Interest Earned', []],
  ['Deposit Returned', []],
  ['Other Income', []],
]

export function TaxonomyTable({ title, taxonomy }: { title: string; taxonomy: [string, string[]][] }) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{title}</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead>Subcategories</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {taxonomy.map(([name, subcategories]) => (
            <TableRow key={name}>
              <TableCell className="whitespace-nowrap font-medium align-top">{name}</TableCell>
              <TableCell className="whitespace-normal break-words text-muted-foreground">
                {subcategories.length ? subcategories.join(', ') : '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function TaxonomyIdeasSection() {
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Taxonomy ideas</CardTitle>
        <CardDescription>
          The default categories a fresh install seeds itself with, kept here for reference. Nothing about it is special
          or protected — rename, merge, split, or delete freely above; this list never changes on its own.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-2">
        <TaxonomyTable title="Expense" taxonomy={DEFAULT_EXPENSE_TAXONOMY} />
        <TaxonomyTable title="Income" taxonomy={DEFAULT_INCOME_TAXONOMY} />
      </CardContent>
    </Card>
  )
}

function ClassificationSection({
  classification,
  categories,
}: {
  classification: CategoryClassification
  categories: Record<string, Category>
}) {
  const createCategory = useCreateCategory()
  const createSubcategory = useCreateSubcategory()
  const renamePreview = useCategoryRenamePreview()
  const renameCategoryMutation = useRenameCategory()
  const deletePreview = useCategoryDeletePreview()
  const deleteCategoryMutation = useDeleteCategory()
  const [newCategoryName, setNewCategoryName] = useState('')
  const [newCategoryError, setNewCategoryError] = useState<string | null>(null)
  const [subcategoryDrafts, setSubcategoryDrafts] = useState<Record<string, string>>({})
  const [subcategoryErrors, setSubcategoryErrors] = useState<Record<string, string>>({})
  // A merging rename needs the user's confirmation (see
  // `useCategoryRenamePreview`) before it commits — while that's pending,
  // `renameResetTick` forces the `InlineNameInput` that triggered it to
  // remount (via its `key`), which is what actually reverts its draft text
  // back to the category's real name on Decline.
  const [pendingRename, setPendingRename] = useState<{
    categoryId: string
    name: string
    targetName: string
    budgetsToDelete: BudgetToDeletePreview[]
  } | null>(null)
  const [renameResetTick, setRenameResetTick] = useState(0)
  // A delete that would uncategorize at least one real posting needs the
  // user's confirmation first (see `useCategoryDeletePreview`) — deleting
  // one with no postings at all just happens immediately, no popup.
  const [pendingDelete, setPendingDelete] = useState<{
    categoryId: string
    categoryName: string
    postingCount: number
  } | null>(null)

  const topLevel = Object.values(categories)
    .filter((category) => category.classification === classification && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  function addCategory() {
    if (!newCategoryName) return
    const color = nextAvailableColor(Object.values(categories).map((category) => category.color))
    createCategory.mutate(
      { name: newCategoryName, classification, color },
      {
        onSuccess: () => {
          setNewCategoryName('')
          setNewCategoryError(null)
        },
        onError: () => setNewCategoryError('This category already exists'),
      },
    )
  }

  async function removeCategory(categoryId: string, categoryName: string) {
    const preview = await deletePreview.mutateAsync(categoryId)
    if (preview.posting_count > 0) {
      setPendingDelete({ categoryId, categoryName, postingCount: preview.posting_count })
      return
    }
    deleteCategoryMutation.mutate(categoryId)
  }

  function acceptPendingDelete() {
    if (!pendingDelete) return
    deleteCategoryMutation.mutate(pendingDelete.categoryId)
    setPendingDelete(null)
  }

  function declinePendingDelete() {
    setPendingDelete(null)
  }

  async function renameCategory(categoryId: string, name: string) {
    const preview = await renamePreview.mutateAsync({ categoryId, name })
    if (preview.will_merge) {
      setPendingRename({
        categoryId,
        name,
        targetName: preview.target_name ?? name,
        budgetsToDelete: preview.budgets_to_delete,
      })
      return
    }
    renameCategoryMutation.mutate({ categoryId, name })
  }

  function acceptPendingRename() {
    if (!pendingRename) return
    renameCategoryMutation.mutate({ categoryId: pendingRename.categoryId, name: pendingRename.name })
    setPendingRename(null)
  }

  function declinePendingRename() {
    setPendingRename(null)
    setRenameResetTick((tick) => tick + 1)
  }

  function addSubcategory(parent: Category) {
    const name = subcategoryDrafts[parent.category_id]?.trim()
    if (!name) return
    const color = nextAvailableColor(Object.values(categories).map((category) => category.color))
    createSubcategory.mutate(
      { parentId: parent.category_id, subcategory: { name, color } },
      {
        onSuccess: () => {
          setSubcategoryDrafts((prev) => ({ ...prev, [parent.category_id]: '' }))
          setSubcategoryErrors((prev) => ({ ...prev, [parent.category_id]: '' }))
        },
        onError: () =>
          setSubcategoryErrors((prev) => ({
            ...prev,
            [parent.category_id]: 'This subcategory already exists in this category',
          })),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="capitalize">{classification}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {topLevel.map((category) => {
          const children = Object.values(categories)
            .filter((sub) => sub.parent_category_id === category.category_id)
            .sort((a, b) => a.name.localeCompare(b.name))
          return (
            <div key={category.category_id} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between">
                <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                  <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: category.color }} />
                  <InlineNameInput
                    key={`${category.category_id}:${renameResetTick}`}
                    value={category.name}
                    onCommit={(name) => renameCategory(category.category_id, name)}
                    className="text-sm font-medium"
                  />
                </span>
                <button
                  onClick={() => removeCategory(category.category_id, category.name)}
                  className="text-muted-foreground/60 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {children.map((child) => {
                  // "Other" is a backend-managed catch-all (see
                  // `store.normalize_categories`) — auto-created the moment
                  // a category gets its first real subcategory, and
                  // auto-removed once no real ones are left, so it's never
                  // something to delete by hand.
                  const isOther = child.category_id.endsWith(':other')
                  return (
                    <Badge key={child.category_id} variant="outline" className="gap-1">
                      {isOther ? (
                        child.name
                      ) : (
                        <InlineNameInput
                          key={`${child.category_id}:${renameResetTick}`}
                          value={child.name}
                          onCommit={(name) => renameCategory(child.category_id, name)}
                          className="h-4 text-xs"
                        />
                      )}
                      {!isOther && (
                        <button
                          onClick={() => removeCategory(child.category_id, child.name)}
                          className="text-muted-foreground/60 hover:text-destructive"
                        >
                          <Trash2 className="size-2.5" />
                        </button>
                      )}
                    </Badge>
                  )
                })}
                <div className="flex flex-col gap-0.5">
                  <Input
                    className="h-6 w-32 text-xs"
                    placeholder="+ subcategory"
                    value={subcategoryDrafts[category.category_id] ?? ''}
                    onChange={(event) => {
                      setSubcategoryDrafts((prev) => ({ ...prev, [category.category_id]: event.target.value }))
                      setSubcategoryErrors((prev) => ({ ...prev, [category.category_id]: '' }))
                    }}
                    onKeyDown={(event) => event.key === 'Enter' && addSubcategory(category)}
                  />
                  {subcategoryErrors[category.category_id] && (
                    <span className="text-xs text-destructive">{subcategoryErrors[category.category_id]}</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-0.5">
            <Input
              className="w-48"
              placeholder="New category name"
              value={newCategoryName}
              onChange={(event) => {
                setNewCategoryName(event.target.value)
                setNewCategoryError(null)
              }}
              onKeyDown={(event) => event.key === 'Enter' && addCategory()}
            />
            {newCategoryError && <span className="text-xs text-destructive">{newCategoryError}</span>}
          </div>
          <Button size="sm" onClick={addCategory}>
            Add category
          </Button>
        </div>
      </CardContent>
      {pendingRename && (
        <Dialog open onOpenChange={(open) => !open && declinePendingRename()}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Merge categories?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              Renaming to '{pendingRename.name}' will merge into the existing category '{pendingRename.targetName}' —
              this can't be undone.
            </p>
            {pendingRename.budgetsToDelete.length > 0 && (
              <p className="text-sm text-destructive">
                '{pendingRename.targetName}' already has a budget for the same month
                {pendingRename.budgetsToDelete.length > 1 ? 's' : ''} — the following will be fully deleted:{' '}
                {pendingRename.budgetsToDelete
                  .map((budget) =>
                    budget.month
                      ? `${formatCurrency(budget.amount, budget.currency)} (${budget.month})`
                      : `${formatCurrency(budget.amount, budget.currency)} (every month)`,
                  )
                  .join(', ')}
                .
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={declinePendingRename}>
                Decline
              </Button>
              <Button onClick={acceptPendingRename}>Accept</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {pendingDelete && (
        <Dialog open onOpenChange={(open) => !open && declinePendingDelete()}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Delete '{pendingDelete.categoryName}'?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-destructive">
              {pendingDelete.postingCount} transaction{pendingDelete.postingCount === 1 ? '' : 's'} currently{' '}
              {pendingDelete.postingCount === 1 ? 'has' : 'have'} this category — deleting it will make{' '}
              {pendingDelete.postingCount === 1 ? 'that transaction' : 'those transactions'} uncategorized. This can't
              be undone.
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={declinePendingDelete}>
                Decline
              </Button>
              <Button variant="destructive" onClick={acceptPendingDelete}>
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  )
}

// --- Category patterns ---------------------------------------------------

function categoriesWithSubcategories(categories: Record<string, Category>): Set<string> {
  const withSubcategories = new Set<string>()
  for (const category of Object.values(categories)) {
    if (category.parent_category_id != null) withSubcategories.add(category.parent_category_id)
  }
  return withSubcategories
}

function categoryName(categories: Record<string, Category>, categoryId: string) {
  return categories[categoryId]?.name ?? categoryId
}

// Every top-level category, grouped by classification — a pattern isn't
// tied to one posting's sign the way categorizing a real transaction is,
// so (unlike `CategorySelect`) it needs access to both sides at once.
function AnyClassificationCategorySelect({
  categories,
  value,
  onChange,
}: {
  categories: Record<string, Category>
  value: string | null
  onChange: (categoryId: string) => void
}) {
  const topLevel = Object.values(categories).filter((category) => category.parent_category_id === null)
  const expense = topLevel
    .filter((category) => category.classification === 'expense')
    .sort((a, b) => a.name.localeCompare(b.name))
  const income = topLevel
    .filter((category) => category.classification === 'income')
    .sort((a, b) => a.name.localeCompare(b.name))
  const items = Object.fromEntries(topLevel.map((category) => [category.category_id, category.name]))

  return (
    <Select value={value ?? undefined} onValueChange={(next) => next && onChange(next)}>
      <SelectTrigger size="sm" className="min-w-44">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Expense</SelectLabel>
          {expense.map((category) => (
            <SelectItem key={category.category_id} value={category.category_id}>
              {category.name}
            </SelectItem>
          ))}
        </SelectGroup>
        <SelectGroup>
          <SelectLabel>Income</SelectLabel>
          {income.map((category) => (
            <SelectItem key={category.category_id} value={category.category_id}>
              {category.name}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function PatternSubcategorySelect({
  categories,
  categoryId,
  value,
  onChange,
}: {
  categories: Record<string, Category>
  categoryId: string | null
  value: string | null
  onChange: (subcategoryId: string) => void
}) {
  const children = Object.values(categories)
    .filter((category) => category.parent_category_id === categoryId)
    .sort((a, b) => a.name.localeCompare(b.name))
  if (!categoryId || children.length === 0) return null
  const items = Object.fromEntries(children.map((c) => [c.category_id, c.name]))
  return (
    <Select value={value ?? undefined} onValueChange={(next) => next && onChange(next)}>
      <SelectTrigger size="sm" className="min-w-40">
        <SelectValue items={items} placeholder="Choose a subcategory" />
      </SelectTrigger>
      <SelectContent>
        {children.map((category) => (
          <SelectItem key={category.category_id} value={category.category_id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function PatternEditDialog({
  pattern,
  categories,
  withSubcategories,
  onClose,
  onSave,
}: {
  pattern: CategoryPattern
  categories: Record<string, Category>
  withSubcategories: Set<string>
  onClose: () => void
  onSave: (pattern: CategoryPattern) => void
}) {
  const [draft, setDraft] = useState(pattern)
  const needsSubcategory = withSubcategories.has(draft.category_id)
  const canSave = draft.description_contains.trim().length > 0 && (!needsSubcategory || draft.subcategory_id !== null)
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
            <AnyClassificationCategorySelect
              categories={categories}
              value={draft.category_id}
              onChange={(categoryId) =>
                setDraft((prev) => ({ ...prev, category_id: categoryId, subcategory_id: null }))
              }
            />
          </label>
          {needsSubcategory && (
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Suggested subcategory (required — this category has subcategories)
              <PatternSubcategorySelect
                categories={categories}
                categoryId={draft.category_id}
                value={draft.subcategory_id ?? null}
                onChange={(subcategoryId) => setDraft((prev) => ({ ...prev, subcategory_id: subcategoryId }))}
              />
            </label>
          )}
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Priority (lower wins ties)
            <NumberInput
              value={draft.priority}
              onCommit={(priority) => setDraft((prev) => ({ ...prev, priority: priority ?? 0 }))}
            />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!canSave}
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
// `pending_source: "pattern"` on postings) — distinct from a transfer
// rule, which resolves a posting's counterparty automatically with no
// confirmation step. A suggestion from here always needs the same
// accept/reject pass in Transactions that an AI suggestion does.
function CategoryPatternsSection({
  patterns,
  categories,
}: {
  patterns: Record<string, CategoryPattern>
  categories: Record<string, Category>
}) {
  const patchPattern = usePatchCategoryPattern()
  const deletePattern = useDeleteCategoryPattern()
  const createPattern = useCreateCategoryPattern()
  const [editing, setEditing] = useState<CategoryPattern | null>(null)
  const [draft, setDraft] = useState<{
    descriptionContains: string
    categoryId: string | null
    subcategoryId: string | null
  }>({
    descriptionContains: '',
    categoryId: null,
    subcategoryId: null,
  })
  const patternList = Object.values(patterns)
  const { sorted, sort, toggleSort } = useSortableRows(patternList, 'priority')
  const withSubcategories = categoriesWithSubcategories(categories)
  const draftNeedsSubcategory = draft.categoryId !== null && withSubcategories.has(draft.categoryId)
  const canAdd =
    draft.descriptionContains.trim().length > 0 &&
    draft.categoryId !== null &&
    (!draftNeedsSubcategory || draft.subcategoryId !== null)

  function addPattern() {
    if (!canAdd || !draft.categoryId) return
    createPattern.mutate({
      description_contains: draft.descriptionContains,
      category_id: draft.categoryId,
      subcategory_id: draft.subcategoryId,
      priority: 100,
    })
    setDraft({ descriptionContains: '', categoryId: null, subcategoryId: null })
  }

  function removePattern(patternId: string) {
    deletePattern.mutate(patternId)
  }

  function savePattern(updated: CategoryPattern) {
    patchPattern.mutate({
      patternId: updated.pattern_id,
      update: {
        description_contains: updated.description_contains,
        category_id: updated.category_id,
        subcategory_id: updated.subcategory_id,
        priority: updated.priority,
        active: updated.active,
        expected_version: patterns[updated.pattern_id].version,
      },
    })
  }

  function togglePatternActive(patternId: string, active: boolean) {
    const existing = patterns[patternId]
    if (!existing) return
    patchPattern.mutate({
      patternId,
      update: {
        description_contains: existing.description_contains,
        category_id: existing.category_id,
        subcategory_id: existing.subcategory_id,
        priority: existing.priority,
        active,
        // Last-write-wins on a fast on/off/on toggle (see the versioning doc); `savePattern` above
        // keeps the real version check for destructive field edits.
        expected_version: null,
      },
    })
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Category patterns</CardTitle>
        <CardDescription>
          A category pattern says: if a posting's description contains this text, suggest this category (and
          subcategory). It doesn't categorize anything by itself — go to Transactions, click Apply on a matching row (or
          run bulk suggestions), then Validate to actually apply the suggestion.
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
              <SortableTableHead
                active={sort.key === 'priority'}
                desc={sort.desc}
                onClick={() => toggleSort('priority')}
              >
                Priority
              </SortableTableHead>
              <TableHead>Active</TableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((pattern) => (
              <TableRow key={pattern.pattern_id} className={pattern.active ? '' : 'opacity-50'}>
                <TableCell className="font-medium">{pattern.description_contains}</TableCell>
                <TableCell className="text-muted-foreground">
                  {categoryName(categories, pattern.category_id)}
                  {pattern.subcategory_id ? ` › ${categoryName(categories, pattern.subcategory_id)}` : ''}
                </TableCell>
                <TableCell className="text-muted-foreground">{pattern.priority}</TableCell>
                <TableCell>
                  <Switch
                    size="sm"
                    checked={pattern.active}
                    onCheckedChange={(checked) => togglePatternActive(pattern.pattern_id, checked)}
                  />
                </TableCell>
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
            <AnyClassificationCategorySelect
              categories={categories}
              value={draft.categoryId}
              onChange={(categoryId) => setDraft((prev) => ({ ...prev, categoryId, subcategoryId: null }))}
            />
          </label>
          {draftNeedsSubcategory && (
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Subcategory (required)
              <PatternSubcategorySelect
                categories={categories}
                categoryId={draft.categoryId}
                value={draft.subcategoryId}
                onChange={(subcategoryId) => setDraft((prev) => ({ ...prev, subcategoryId }))}
              />
            </label>
          )}
          <Button size="sm" onClick={addPattern} disabled={!canAdd}>
            Add pattern
          </Button>
        </div>
      </CardContent>
      {editing && (
        <PatternEditDialog
          pattern={editing}
          categories={categories}
          withSubcategories={withSubcategories}
          onClose={() => setEditing(null)}
          onSave={savePattern}
        />
      )}
    </Card>
  )
}

export function CategoriesTab({
  categories,
  patterns,
}: {
  categories: Record<string, Category>
  patterns: Record<string, CategoryPattern>
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'categories'
  const setTab = (value: string) => setSearchParams(value === 'categories' ? {} : { tab: value })

  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList>
        <TabsTrigger value="categories">Categories</TabsTrigger>
        <TabsTrigger value="patterns">Patterns</TabsTrigger>
        <TabsTrigger value="reference">Reference</TabsTrigger>
      </TabsList>
      <TabsContent value="categories" className="grid gap-6 pt-2 lg:grid-cols-2">
        <ClassificationSection classification="expense" categories={categories} />
        <ClassificationSection classification="income" categories={categories} />
      </TabsContent>
      <TabsContent value="patterns" className="pt-2">
        <CategoryPatternsSection patterns={patterns} categories={categories} />
      </TabsContent>
      <TabsContent value="reference" className="pt-2">
        <TaxonomyIdeasSection />
      </TabsContent>
    </Tabs>
  )
}
