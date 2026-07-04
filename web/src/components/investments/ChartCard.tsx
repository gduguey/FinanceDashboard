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
  children,
}: {
  title: string
  titleTooltip?: GlossaryTerm
  description?: string
  action?: ReactNode
  legend?: ReactNode
  isLoading?: boolean
  isEmpty?: boolean
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
          <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
            No data yet
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
