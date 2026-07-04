import { Info } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { GLOSSARY, type GlossaryTerm } from '@/lib/glossary'

// A small "i" icon that explains a technical term on hover — short,
// non-jargony, and specific to what the number includes/excludes on this
// dashboard, per the term's entry in lib/glossary.ts.
export function InfoTooltip({ term }: { term: GlossaryTerm }) {
  const entry = GLOSSARY[term]
  return (
    <Tooltip>
      <TooltipTrigger className="inline-flex align-middle text-muted-foreground/70 hover:text-foreground">
        <Info className="size-3.5" />
      </TooltipTrigger>
      <TooltipContent className="max-w-64 text-pretty">
        <p className="font-medium">{entry.title}</p>
        <p className="mt-0.5 font-normal opacity-90">{entry.body}</p>
      </TooltipContent>
    </Tooltip>
  )
}
