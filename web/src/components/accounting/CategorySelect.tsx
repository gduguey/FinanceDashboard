import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Category, CategoryClassification } from '@/types/accounting'

export const NO_CATEGORY = '__none__'

// Passes an explicit `value -> label` map to `SelectValue` (see its own
// comment in components/ui/select.tsx) so the selected category shows its
// display name, never its raw id.
export function CategorySelect({
  categories,
  classification,
  value,
  onChange,
}: {
  categories: Record<string, Category>
  classification: CategoryClassification
  value: string | null
  onChange: (categoryId: string | null) => void
}) {
  const topLevel = Object.values(categories)
    .filter((category) => category.classification === classification && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))
  const items = { [NO_CATEGORY]: 'Uncategorized', ...Object.fromEntries(topLevel.map((c) => [c.category_id, c.name])) }
  return (
    <Select value={value ?? NO_CATEGORY} onValueChange={(next) => onChange(next === NO_CATEGORY ? null : next)}>
      <SelectTrigger size="sm" className="w-44">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_CATEGORY}>Uncategorized</SelectItem>
        {topLevel.map((category) => (
          <SelectItem key={category.category_id} value={category.category_id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

// Once a category has any subcategories, the backend guarantees it also has
// an "Other" catch-all (see `accounting.store.normalize_categories`) — so
// "None" is never offered here: a category with subcategories always has a
// selectable one, even if it's just "Other".
export function SubcategorySelect({
  categories,
  categoryId,
  value,
  onChange,
}: {
  categories: Record<string, Category>
  categoryId: string | null
  value: string | null
  onChange: (subcategoryId: string | null) => void
}) {
  const children = Object.values(categories)
    .filter((category) => category.parent_category_id === categoryId)
    .sort((a, b) => a.name.localeCompare(b.name))
  if (!categoryId || children.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>
  }
  const items = Object.fromEntries(children.map((c) => [c.category_id, c.name]))
  return (
    <Select value={value ?? undefined} onValueChange={(next) => next && onChange(next)}>
      <SelectTrigger size="sm" className="w-40">
        <SelectValue items={items} />
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
