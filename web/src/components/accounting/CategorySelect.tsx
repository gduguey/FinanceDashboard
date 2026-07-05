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
  const items = { [NO_CATEGORY]: 'None', ...Object.fromEntries(children.map((c) => [c.category_id, c.name])) }
  if (!categoryId || children.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>
  }
  return (
    <Select value={value ?? NO_CATEGORY} onValueChange={(next) => onChange(next === NO_CATEGORY ? null : next)}>
      <SelectTrigger size="sm" className="w-40">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_CATEGORY}>None</SelectItem>
        {children.map((category) => (
          <SelectItem key={category.category_id} value={category.category_id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
