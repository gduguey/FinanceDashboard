import { cn } from '@/lib/utils'

// A `truncate` (or `line-clamp-*`) class alone hides whatever doesn't fit,
// with no way to read the rest — the browser's native `title` attribute
// reveals it on hover for free, no tooltip library or extra markup needed.
// Use this instead of hand-rolling `className="truncate"` wherever cropped
// text can plausibly be worth reading in full (descriptions, filenames,
// names, account labels, ...).
export function Truncate({ text, className }: { text: string; className?: string }) {
  return (
    <span className={cn('block truncate', className)} title={text}>
      {text}
    </span>
  )
}
