import type { ReactNode } from 'react'
import { PageErrorFallback } from '@/components/shared/PageErrorFallback'
import { useAccountingStore } from '@/hooks/useAccountingData'

/**
 * Show the shared error page instead of a Money page whose data cannot load.
 *
 * The whole Money side reads from the same underlying data layer — if the core
 * store call is failing outright (e.g. the deploy VM's `data/` bind mount got
 * recreated root-owned by Docker, unreadable by the non-root `appuser` the
 * container actually runs as, until a one-time `chown` fixes it), every one of
 * these pages would too, each in its own slightly different way, some of them
 * blank rather than erroring. One fallback here, instead of trusting every
 * page's own error handling to catch it.
 *
 * Reads the store query directly rather than taking the error as a prop: it is
 * the same cached query the shell already holds, so this costs no request, and
 * it lets `routeTable` apply the gate without threading state through the lazy
 * boundary.
 */
export function MoneyPage({ children }: { children: ReactNode }) {
  const { isError } = useAccountingStore()
  return isError ? <PageErrorFallback /> : children
}
