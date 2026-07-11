import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// The app-wide header spec. Every page renders exactly one `PageHeader`, as
// the first child of its own `overflow-y-auto` scroll container — never
// inside a scrolling area itself, so it stays pinned (`sticky top-0`) while
// that page's own content scrolls under it. It's built from up to three
// stacked rows; a page uses only the rows it needs, but never invents a
// fourth kind of its own or reaches for a different component to do a
// header row's job:
//
//   1. Title + actions (always present). The title on the left never
//      changes for a given page. `actions`, on the right, are page-level
//      *commands* — a currency toggle, a "sync now" button — never
//      navigation of any kind.
//   2. `controls` (optional). Page-scoped settings/filters that apply
//      regardless of whichever tab is active, if the page has tabs at all
//      (e.g. Investments' tax-tracking toggle) — rare, and never itself a
//      navigation control.
//   3. Exactly one of the following, never both:
//        - Real tabs (`Tabs`/`TabsList`/`TabsContent` from `ui/tabs`),
//          rendered by the page itself in its scrolling body, immediately
//          below `PageHeader` — for switching between genuinely different
//          views that were never meant to be seen together (Investments'
//          own Overview vs. Performance vs. Allocation). Only one of
//          these views is ever mounted at a time.
//        - `sections`: an anchor-link nav rendered *by PageHeader itself*
//          — for one continuous scrolling page broken into thematic
//          areas that are all part of the same view at once, just
//          deep-linkable (the Guide; Net Worth's own Summary/History/
//          Allocation/Accounts/etc.). Every section named here must
//          already be mounted and visible in the current scroll, or the
//          links are dead.
// ---------------------------------------------------------------------------

export interface PageHeaderSection {
  id: string
  label: string
}

export function PageHeader({
  title,
  actions,
  controls,
  sections,
}: {
  title: string
  actions?: ReactNode
  controls?: ReactNode
  sections?: PageHeaderSection[]
}) {
  return (
    <div className="sticky top-0 z-10 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="flex items-center justify-between px-8 py-5">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {controls && <div className="flex items-center gap-6 border-t border-border/50 px-8 py-3">{controls}</div>}
      {sections && sections.length > 0 && (
        <nav className="flex flex-wrap gap-x-5 gap-y-1 px-8 pb-3 text-sm text-muted-foreground">
          {sections.map((section) => (
            <a key={section.id} href={`#${section.id}`} className="transition-colors hover:text-foreground">
              {section.label}
            </a>
          ))}
        </nav>
      )}
    </div>
  )
}
