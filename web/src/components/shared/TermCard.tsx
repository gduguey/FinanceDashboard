import type { ReactNode } from 'react'

// One glossary-style term, as its own bordered card rather than a bare
// `dt`/`dd` pair — used both by the Glossary page's term grid and by
// Taxes' "How it's taxed" tab, so a definition looks the same wherever it
// shows up instead of each page inventing its own treatment.
export function TermCard({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-foreground/10 bg-muted/30 p-4">
      <dt className="text-sm font-semibold text-foreground">{term}</dt>
      <dd className="mt-1 text-sm leading-relaxed text-muted-foreground">{children}</dd>
    </div>
  )
}
