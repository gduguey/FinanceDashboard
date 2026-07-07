import type { ReactElement, ReactNode } from 'react'
import { ResponsiveContainer } from 'recharts'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import type { GlossaryTerm } from '@/lib/glossary'

export function ChartCard({
  title,
  titleTooltip,
  description,
  action,
  legend,
  isLoading,
  isEmpty,
  error,
  children,
}: {
  title: string
  titleTooltip?: GlossaryTerm
  description?: string
  action?: ReactNode
  legend?: ReactNode
  isLoading?: boolean
  isEmpty?: boolean
  // The backend's own error message (e.g. "No ledger cached yet. Hit Sync
  // to pull it from IBKR.") — shown instead of the generic "No data yet"
  // whenever the empty state was actually caused by a failed request, so
  // "never synced" and "sync failed" no longer read as the same thing.
  error?: string | null
  children: ReactElement
}) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          {title}
          {titleTooltip && <InfoTooltip term={titleTooltip} />}
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
        {action && <CardAction>{action}</CardAction>}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-72 w-full" />
        ) : isEmpty ? (
          <div className="flex h-72 items-center justify-center px-6 text-center text-sm text-muted-foreground">
            {error || 'No data yet'}
          </div>
        ) : (
          <>
            {legend}
            <ResponsiveContainer width="100%" height={288}>
              {children}
            </ResponsiveContainer>
          </>
        )}
      </CardContent>
    </Card>
  )
}
