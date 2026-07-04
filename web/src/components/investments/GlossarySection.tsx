import { Compass } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { GLOSSARY } from '@/lib/glossary'

// The "why" behind this page, in plain language — placed right before the
// glossary since the two answer related questions: what is this page
// trying to show, and what do the words on it mean.
function PageMentality() {
  return (
    <Card className="border-foreground/10 bg-gradient-to-br from-muted/60 to-transparent">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Compass className="size-4 text-muted-foreground" />
          How this page thinks
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm leading-relaxed text-muted-foreground">
        <p>
          This page exists to answer one question honestly:{' '}
          <span className="text-foreground">
            is investing actually working, separate from the fact that you keep putting money in?
          </span>{' '}
          A rising account balance is easy to mistake for a good decision, when most of the rise might just be your
          last paycheck landing in the account. Every number here is built to keep those two things apart.
        </p>
        <p>
          It does that by treating your deposits and withdrawals as a first-class fact — the{' '}
          <span className="text-foreground">ledger</span> — and replaying that exact history against a few
          alternatives: what if that same money had sat in a savings account instead, or bought an index fund
          instead. The gap between what actually happened and those counterfactuals is the answer. From the same
          ledger, the page also reconstructs every individual purchase as a{' '}
          <span className="text-foreground">lot</span>, so gains can be traced back to specific holdings and specific
          holding periods rather than a single blended number.
        </p>
      </CardContent>
    </Card>
  )
}

// Every term used across the tooltips on this page, spelled out in full —
// for anyone who'd rather read definitions once than hover icon by icon.
// Alphabetical, so it reads like a reference rather than a tour.
export function GlossarySection() {
  const entries = Object.values(GLOSSARY).sort((a, b) => a.title.localeCompare(b.title))
  return (
    <div className="space-y-6">
      <PageMentality />
      <dl className="grid grid-cols-1 gap-x-10 gap-y-5 md:grid-cols-2 xl:grid-cols-3">
        {entries.map((entry) => (
          <div key={entry.title}>
            <dt className="text-sm font-medium text-foreground">{entry.title}</dt>
            <dd className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{entry.body}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
