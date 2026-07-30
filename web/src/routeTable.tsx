import { type ComponentType, lazy } from 'react'
import { MoneyPage } from '@/components/layout/MoneyPage'
import { routePathFromFile } from '@/lib/routing'

interface RouteModule {
  default: ComponentType
  requiresStore: boolean
}

// One file under src/routes/ = one page — see routePathFromFile for how its
// path maps to a URL. Deliberately *not* `{ eager: true }`: eager glob pulls
// every page and its whole component subtree into the entry chunk, so landing
// on the overview paid for the guide, the importer and the tax panel too.
// Each of these is now its own chunk, fetched when its route is first visited.
const routeModules = import.meta.glob<RouteModule>('./routes/**/*.tsx')

/**
 * Wrap a route module loader so the page arrives already gated on the store.
 *
 * `requiresStore` lives in the route module, which under lazy loading is not
 * readable until the chunk has arrived — so the gate is applied inside the
 * loader, where the flag is in hand, rather than by the caller. `lazy` caches
 * the resolved module, so the component identity this produces is created once
 * and stays stable across renders.
 *
 * One behavioural note: a store-load failure now shows `PageErrorFallback`
 * *after* the route chunk has been fetched, where the eager version could
 * short-circuit before mounting anything. The fetch is wasted only in the case
 * where the backend is already failing, which is not a case worth optimising.
 */
function lazyRoute(load: () => Promise<RouteModule>) {
  return lazy(async () => {
    const routeModule = await load()
    const Page = routeModule.default
    if (!routeModule.requiresStore) return { default: Page }
    return {
      default: () => (
        <MoneyPage>
          <Page />
        </MoneyPage>
      ),
    }
  })
}

export interface AppRoute {
  path: string
  Component: ComponentType
}

/**
 * Every page in the app, as a URL path and the lazy component serving it.
 *
 * Sorted by path so the table is stable to read and to diff; React Router
 * matches by specificity rather than declaration order, so the sort is
 * presentational only.
 */
export const routes: AppRoute[] = Object.entries(routeModules)
  .map(([file, load]) => ({ path: routePathFromFile(file), Component: lazyRoute(load) }))
  .sort((a, b) => a.path.localeCompare(b.path))
