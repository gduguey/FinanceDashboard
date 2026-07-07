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
//          views that were never meant to be seen together (Accounting's
//          Dashboard vs. Transactions vs. Categories; Investments' live
//          Dashboard vs. static Reference material). Only one of these
//          views is ever mounted at a time.
//        - `sections`: an anchor-link nav rendered *by PageHeader itself*
//          — for one continuous scrolling page broken into thematic
//          areas that are all part of the same view at once, just
//          deep-linkable (the Guide; Investments' own Dashboard tab:
//          Overview/Performance/Allocation/Lots). Every section named
//          here must already be mounted and visible in the current
//          scroll, or the links are dead.
//
// A page with real tabs *and* deep-linkable sections within one of those
// tabs (Investments is the only current example) must tag each section
// with which tab it belongs to via `PageHeaderSection.tab`, and pass that
// tab's own value as `activeTab` — `PageHeader` filters `sections` down to
// the active tab's own before rendering. This is enforced structurally
// here (not left to each page to remember) specifically because leaving it
// to the page is exactly how Investments used to leak its Dashboard tab's
// section links into the Reference tab, where none of those anchors
// existed anymore.
// ---------------------------------------------------------------------------

export interface PageHeaderSection {
  id: string
  label: string
  // Which of the page's own tabs this section's anchor belongs to — omit
  // on a page with no tabs, or on a page whose tabs have no per-tab
  // sections at all. A section tagged for a tab other than `activeTab`
  // is hidden; see this file's own module doc for why.
  tab?: string
}

export function PageHeader({
  title,
  actions,
  controls,
  sections,
  activeTab,
}: {
  title: string
  actions?: ReactNode
  controls?: ReactNode
  sections?: PageHeaderSection[]
  // The page's currently-selected tab — only meaningful (and only
  // needed) when at least one entry in `sections` sets `tab`.
  activeTab?: string
}) {
  const visibleSections = sections?.filter((section) => section.tab === undefined || section.tab === activeTab)

  return (
    <div className="sticky top-0 z-10 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="flex items-center justify-between px-8 py-5">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {controls && <div className="flex items-center gap-6 border-t border-border/50 px-8 py-3">{controls}</div>}
      {visibleSections && visibleSections.length > 0 && (
        <nav className="flex flex-wrap gap-x-5 gap-y-1 px-8 pb-3 text-sm text-muted-foreground">
          {visibleSections.map((section) => (
            <a key={section.id} href={`#${section.id}`} className="transition-colors hover:text-foreground">
              {section.label}
            </a>
          ))}
        </nav>
      )}
    </div>
  )
}
