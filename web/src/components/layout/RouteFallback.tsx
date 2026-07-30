import { Skeleton } from '@/components/ui/skeleton'

/**
 * What fills the page area while a route's chunk is in flight.
 *
 * Shaped like the header-plus-content every page renders, so arriving at a
 * page does not shift the layout once its chunk lands. Pages fetch their own
 * data after mounting and show their own skeletons for it; this one covers
 * only the gap before the page component exists at all.
 */
export function RouteFallback() {
  return (
    <div className="flex-1 overflow-y-auto" aria-busy="true" aria-label="Loading page">
      <div className="border-b px-8 py-6">
        <Skeleton className="h-7 w-48" />
      </div>
      <div className="mx-auto max-w-7xl space-y-6 px-8 py-8">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  )
}
