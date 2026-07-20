import { useState } from 'react'

// Shared "click a row to expand a detail section below it, with a
// collapse-all/unfold-all toggle up top" behavior — more than one row can
// be expanded at once, unlike a single-open accordion.
export function useExpandableRows(ids: string[]) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function collapseAll() {
    setExpanded(new Set())
  }

  function unfoldAll() {
    setExpanded(new Set(ids))
  }

  return { expanded, toggle, collapseAll, unfoldAll }
}
