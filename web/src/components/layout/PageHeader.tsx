import type { ReactNode } from 'react'

const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'performance', label: 'Performance' },
  { id: 'allocation', label: 'Allocation' },
  { id: 'lots', label: 'Lots' },
  { id: 'taxes', label: 'Taxes', conditional: true },
  { id: 'data-quality', label: 'Data quality' },
  { id: 'glossary', label: 'Glossary' },
]

export function PageHeader({ title, actions, controls, taxEnabled = false }: { title: string; actions?: ReactNode; controls?: ReactNode; taxEnabled?: boolean }) {
  return (
    <div className="sticky top-0 z-10 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="flex items-center justify-between px-8 py-5">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
        {actions}
      </div>
      {controls && <div className="flex items-center gap-6 border-t border-border/50 px-8 py-3">{controls}</div>}
      <nav className="flex gap-5 px-8 pb-3 text-sm text-muted-foreground">
        {SECTIONS.filter((section) => !section.conditional || taxEnabled).map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            className="transition-colors hover:text-foreground"
          >
            {section.label}
          </a>
        ))}
      </nav>
    </div>
  )
}
