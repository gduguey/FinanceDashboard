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
    // `role="status"` rather than a bare `<div>`: `aria-label` says nothing on
    // a generic role, so a screen reader announced neither the name nor the
    // fact that anything was loading.
    //
    // Deliberately no `aria-busy="true"`. That attribute means "this region is
    // mid-update, hold off announcing it until I'm done", and it only works if
    // something later sets it back to false. This element never does: it mounts
    // already showing its final content and is unmounted outright the moment
    // the route's chunk resolves. Marking it busy for its whole lifetime would
    // tell assistive tech to withhold the announcement until an update that
    // never arrives, swallowing the very message the region exists to deliver.
    <div className="flex-1 overflow-y-auto" role="status" aria-label="Loading page">
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
