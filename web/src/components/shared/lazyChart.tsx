import { type ComponentType, lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * Defer a chart's code until the chart is actually rendered.
 *
 * Recharts and its d3/redux/decimal dependencies are ~109 kB brotli — by far
 * the heaviest thing the app ships, and the overview route is the one route a
 * signed-in user always lands on. Loading it with the route made the landing
 * pay for every chart before anything appeared. Loading it *at* the chart lets
 * the page's non-chart content (health strip, alerts, sync status) paint while
 * the chart code is still arriving.
 *
 * This costs the charts nothing perceptually: they cannot render before their
 * data query resolves either way, and a same-origin chunk beats a database
 * aggregate almost every time. The two now happen in parallel instead of the
 * code being a prerequisite for starting the fetch.
 *
 * Takes a loader that returns the component itself rather than a module with a
 * default export, because every chart in this codebase is a named export.
 * Pair it with `import type` at the call site to keep full prop typing without
 * a runtime import — a type-only import is erased, so it does not undo the
 * split.
 */
export function lazyChart<P extends object>(load: () => Promise<ComponentType<P>>, className = 'h-64 w-full') {
  const Loaded = lazy(async () => ({ default: await load() }))
  return function LazyChart(props: P) {
    // A skeleton the same size as the chart, so the surrounding layout does
    // not jump when the chunk lands.
    return (
      <Suspense fallback={<Skeleton className={className} />}>
        <Loaded {...props} />
      </Suspense>
    )
  }
}
