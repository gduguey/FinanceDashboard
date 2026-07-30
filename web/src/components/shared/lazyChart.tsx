import { type ComponentProps, type ComponentType, lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * Defer a chart's code until the chart is actually rendered.
 *
 * Recharts and its d3, redux and decimal dependencies come to ~109 kB brotli —
 * by far the heaviest thing the app ships, and until this existed every page
 * holding a chart paid for all of it before rendering anything. Loading it *at*
 * the chart lets the page's own content paint while the chart code is still
 * arriving.
 *
 * This costs the charts nothing perceptually: a chart cannot render before its
 * data query resolves either way, and a same-origin chunk beats a database
 * aggregate almost every time. The two now happen in parallel instead of the
 * code being a prerequisite for starting the fetch.
 *
 * The component type is inferred from the loader, so a call site needs no type
 * annotation and — importantly — no import of the chart module itself. Any
 * ordinary `import` of the chart would put it straight back on the page's
 * critical path; `web/tooling/budget.ts` fails the build when that happens.
 *
 * @param load Resolves to the chart component. Charts here are named exports,
 *   so this is usually `() => import('...').then((m) => m.Chart)`.
 * @param className Sizing for the placeholder, matched to the chart it stands
 *   in for so the layout does not jump when the chunk lands.
 */
// Deliberately `any` rather than `never` or `unknown`: this stands for "some
// component, whatever its props", and the props are only ever forwarded
// unexamined. Both stricter spellings make the forwarding itself untypable.
// biome-ignore lint/suspicious/noExplicitAny: props pass straight through, never inspected
type SomeComponent = ComponentType<any>

export function lazyChart<T extends SomeComponent>(load: () => Promise<T>, className = 'h-64 w-full'): T {
  const Loaded = lazy(async () => ({ default: await load() })) as SomeComponent
  function LazyChart(props: ComponentProps<T>) {
    return (
      <Suspense fallback={<Skeleton className={className} />}>
        <Loaded {...props} />
      </Suspense>
    )
  }
  // The wrapper takes and forwards exactly `T`'s props, which is what makes it
  // a drop-in replacement; TypeScript cannot see that through `lazy`'s own
  // generic, so the equivalence is asserted here rather than at every call.
  return LazyChart as unknown as T
}
