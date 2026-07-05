import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useSetCategories } from '@/hooks/useAccountingData'
import type { Category, CategoryClassification } from '@/types/accounting'

const PALETTE = ['#e99537', '#4da568', '#6471eb', '#db5a54', '#df4e92', '#c44fe9', '#eb5429', '#61c9ea']

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
  const [newCategoryName, setNewCategoryName] = useState('')
  const [subcategoryDrafts, setSubcategoryDrafts] = useState<Record<string, string>>({})

  const topLevel = Object.values(categories)
    .filter((category) => category.classification === classification && category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  function addCategory() {
    if (!newCategoryName) return
    const id = `${classification}:${slugify(newCategoryName)}`
    const color = PALETTE[topLevel.length % PALETTE.length]
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

  function addSubcategory(parent: Category) {
    const name = subcategoryDrafts[parent.category_id]?.trim()
    if (!name) return
    const id = `${parent.category_id}:${slugify(name)}`
    setCategories.mutate({
      ...categories,
      [id]: { category_id: id, name, classification, parent_category_id: parent.category_id, color: parent.color },
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
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  <span className="inline-block size-2 rounded-full" style={{ background: category.color }} />
                  {category.name}
                </span>
                <button
                  onClick={() => removeCategory(category.category_id)}
                  className="text-muted-foreground/60 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {children.map((child) => (
                  <Badge key={child.category_id} variant="outline" className="gap-1">
                    {child.name}
                    <button onClick={() => removeCategory(child.category_id)} className="text-muted-foreground/60 hover:text-destructive">
                      <Trash2 className="size-2.5" />
                    </button>
                  </Badge>
                ))}
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

export function CategoriesTab({ categories }: { categories: Record<string, Category> }) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <ClassificationSection classification="expense" categories={categories} />
      <ClassificationSection classification="income" categories={categories} />
    </div>
  )
}

