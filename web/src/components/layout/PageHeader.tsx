import type { ReactNode } from 'react'

export interface PageHeaderSection {
  id: string
  label: string
}

// The one header shape every page in the app renders — sticky, blurred
// on scroll, a title on the left and this page's own actions (currency
// toggle, sync button, whatever it needs) on the right, an optional
// second row of page-specific controls, and an optional third row of
// in-page anchor links for a long scrolling page (Investments, the
// Guide). A page that needs none of the optional rows just omits them —
// the goal is that every page's header looks and behaves identically
// wherever it *can*, not that every page has identical content.
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
