import { useEffect, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHeader, TableRow, TableHead } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useRenameCategory, useSetCategories, useSetCategoryPatterns } from '@/hooks/useAccountingData'
import { nextAvailableColor } from '@/lib/colors'
import { cn } from '@/lib/utils'
import type { Category, CategoryClassification, CategoryPattern } from '@/types/accounting'

// An inline click-to-rename field — looks like plain text until focused, at
// which point it grows to fit what's typed (`field-sizing-content`) rather
// than reflowing the row around a fixed-width box. Commits on blur/Enter,
// reverts on Escape or an empty result (a category always needs a name).
function InlineNameInput({
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
          The default categories a fresh install seeds itself with, kept here for reference. Nothing about it is
          special or protected — rename, merge, split, or delete freely above; this list never changes on its own.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-2">
        <TaxonomyTable title="Expense" taxonomy={DEFAULT_EXPENSE_TAXONOMY} />
        <TaxonomyTable title="Income" taxonomy={DEFAULT_INCOME_TAXONOMY} />
      </CardContent>
    </Card>
  )
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

function ClassificationSection({
  classification,
  categories,
}: {
  classification: CategoryClassification
  categories: Record<string, Category>
}) {
  const setCategories = useSetCategories()
  const renameCategoryMutation = useRenameCategory()
  const [newCategoryName, setNewCategoryName] = useState('')
  const [subcategoryDrafts, setSubcategoryDrafts] = useState<Record<string, string>>({})

  const topLevel = Object.values(categories)
    .filter((category) => category.classification === classification && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  function addCategory() {
    if (!newCategoryName) return
    const id = `${classification}:${slugify(newCategoryName)}`
    const color = nextAvailableColor(Object.values(categories).map((category) => category.color))
    setCategories.mutate({
      ...categories,
      [id]: { category_id: id, name: newCategoryName, classification, parent_category_id: null, color },
    })
    setNewCategoryName('')
  }

  function removeCategory(categoryId: string) {
    const next = { ...categories }
    delete next[categoryId]
    for (const [id, category] of Object.entries(next)) {
      if (category.parent_category_id === categoryId) delete next[id]
    }
    setCategories.mutate(next)
  }

  function renameCategory(categoryId: string, name: string) {
    renameCategoryMutation.mutate({ categoryId, name })
  }

  function addSubcategory(parent: Category) {
    const name = subcategoryDrafts[parent.category_id]?.trim()
    if (!name) return
    const id = `${parent.category_id}:${slugify(name)}`
    const color = nextAvailableColor(Object.values(categories).map((category) => category.color))
    setCategories.mutate({
      ...categories,
      [id]: { category_id: id, name, classification, parent_category_id: parent.category_id, color },
    })
    setSubcategoryDrafts((prev) => ({ ...prev, [parent.category_id]: '' }))
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
                    value={category.name}
                    onCommit={(name) => renameCategory(category.category_id, name)}
                    className="text-sm font-medium"
                  />
                </span>
                <button
                  onClick={() => removeCategory(category.category_id)}
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
                          value={child.name}
                          onCommit={(name) => renameCategory(child.category_id, name)}
                          className="h-4 text-xs"
                        />
                      )}
                      {!isOther && (
                        <button onClick={() => removeCategory(child.category_id)} className="text-muted-foreground/60 hover:text-destructive">
                          <Trash2 className="size-2.5" />
                        </button>
                      )}
                    </Badge>
                  )
                })}
                <Input
                  className="h-6 w-32 text-xs"
                  placeholder="+ subcategory"
                  value={subcategoryDrafts[category.category_id] ?? ''}
                  onChange={(event) => setSubcategoryDrafts((prev) => ({ ...prev, [category.category_id]: event.target.value }))}
                  onKeyDown={(event) => event.key === 'Enter' && addSubcategory(category)}
                />
              </div>
            </div>
          )
        })}
        <div className="flex items-end gap-2">
          <Input
            className="w-48"
            placeholder="New category name"
            value={newCategoryName}
            onChange={(event) => setNewCategoryName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && addCategory()}
          />
          <Button size="sm" onClick={addCategory}>
            Add category
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

// --- Category patterns ---------------------------------------------------

function categoriesWithSubcategories(categories: Record<string, Category>): Set<string> {
  const withSubcategories = new Set<string>()
  for (const category of Object.values(categories)) {
    if (category.parent_category_id !== null) withSubcategories.add(category.parent_category_id)
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
  const expense = topLevel.filter((category) => category.classification === 'expense').sort((a, b) => a.name.localeCompare(b.name))
  const income = topLevel.filter((category) => category.classification === 'income').sort((a, b) => a.name.localeCompare(b.name))
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
              onChange={(categoryId) => setDraft((prev) => ({ ...prev, category_id: categoryId, subcategory_id: null }))}
            />
          </label>
          {needsSubcategory && (
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Suggested subcategory (required — this category has subcategories)
              <PatternSubcategorySelect
                categories={categories}
                categoryId={draft.category_id}
                value={draft.subcategory_id}
                onChange={(subcategoryId) => setDraft((prev) => ({ ...prev, subcategory_id: subcategoryId }))}
              />
            </label>
          )}
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
function CategoryPatternsSection({ patterns, categories }: { patterns: Record<string, CategoryPattern>; categories: Record<string, Category> }) {
  const setPatterns = useSetCategoryPatterns()
  const [editing, setEditing] = useState<CategoryPattern | null>(null)
  const [draft, setDraft] = useState<{ descriptionContains: string; categoryId: string | null; subcategoryId: string | null }>({
    descriptionContains: '',
    categoryId: null,
    subcategoryId: null,
  })
  const patternList = Object.values(patterns)
  const { sorted, sort, toggleSort } = useSortableRows(patternList, 'priority')
  const withSubcategories = categoriesWithSubcategories(categories)
  const draftNeedsSubcategory = draft.categoryId !== null && withSubcategories.has(draft.categoryId)
  const canAdd = draft.descriptionContains.trim().length > 0 && draft.categoryId !== null && (!draftNeedsSubcategory || draft.subcategoryId !== null)

  function addPattern() {
    if (!canAdd || !draft.categoryId) return
    const patternId = `pattern:${Date.now()}`
    const pattern: CategoryPattern = {
      pattern_id: patternId,
      description_contains: draft.descriptionContains,
      category_id: draft.categoryId,
      subcategory_id: draft.subcategoryId,
      priority: 100,
      active: true,
    }
    setPatterns.mutate({ ...patterns, [patternId]: pattern })
    setDraft({ descriptionContains: '', categoryId: null, subcategoryId: null })
  }

  function removePattern(patternId: string) {
    const { [patternId]: _removed, ...rest } = patterns
    setPatterns.mutate(rest)
  }

  function savePattern(updated: CategoryPattern) {
    setPatterns.mutate({ ...patterns, [updated.pattern_id]: updated })
  }

  function togglePatternActive(patternId: string, active: boolean) {
    const existing = patterns[patternId]
    if (!existing) return
    setPatterns.mutate({ ...patterns, [patternId]: { ...existing, active } })
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Category patterns</CardTitle>
        <CardDescription>
          A category pattern says: if a posting's description contains this text, suggest this category (and
          subcategory). It doesn't categorize anything by itself — go to Transactions, click Apply on a matching row
          (or run bulk suggestions), then Validate to actually apply the suggestion.
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
