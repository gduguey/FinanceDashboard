import type { ReactElement, ReactNode } from 'react'
import { ResponsiveContainer } from 'recharts'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function ChartCard({
  title,
  description,
  action,
  isLoading,
  isEmpty,
  children,
}: {
  title: string
  description?: string
  action?: ReactNode
  isLoading?: boolean
  isEmpty?: boolean
  children: ReactElement
}) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
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
          <ResponsiveContainer width="100%" height={288}>
            {children}
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
