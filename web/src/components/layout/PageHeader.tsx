import type { ReactNode } from 'react'

const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'performance', label: 'Performance' },
  { id: 'allocation', label: 'Allocation' },
  { id: 'lots', label: 'Lots' },
  { id: 'taxes', label: 'Taxes' },
  { id: 'data-quality', label: 'Data quality' },
  { id: 'glossary', label: 'Glossary' },
]

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="sticky top-0 z-10 border-b border-border bg-white/95 backdrop-blur-sm">
      <div className="flex items-center justify-between px-8 py-5">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
        {actions}
      </div>
      <nav className="flex gap-5 px-8 pb-3 text-sm text-muted-foreground">
        {SECTIONS.map((section) => (
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
