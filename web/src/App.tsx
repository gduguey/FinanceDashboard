import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComponentType, ReactNode } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Toaster, toast } from 'sonner'
import { AuthGate } from '@/components/layout/AuthGate'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { OnboardingModal } from '@/components/layout/OnboardingModal'
import { Sidebar } from '@/components/layout/Sidebar'
import { PageErrorFallback } from '@/components/shared/PageErrorFallback'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAccountingStore } from '@/hooks/useAccountingData'
import { useSyncBrowserTimezone } from '@/hooks/usePortfolioData'
import { StoreVersionConflictError } from '@/lib/accountingApi'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { routePathFromFile } from '@/lib/routing'

interface RouteModule {
  default: ComponentType
  requiresStore: boolean
}

// One file under src/routes/ = one page — see routePathFromFile for how
// its path maps to a URL. `requiresStore` says whether that page depends
// on the accounting store (and should fall back to PageErrorFallback if
// it failed to load) — see AppShell below.
const routeModules = import.meta.glob<RouteModule>('./routes/**/*.tsx', { eager: true })

const routes = Object.entries(routeModules)
  .map(([file, mod]) => ({
    path: routePathFromFile(file),
    Component: mod.default,
    requiresStore: mod.requiresStore,
  }))
  .sort((a, b) => a.path.localeCompare(b.path))

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  // One place, not one per button: every `useMutation` in the app
  // surfaces its failure here automatically, unless it opts out with its
  // own `onError`. Without this, a failed save just silently doesn't
  // happen — the exact "bad key still shows Connected" confusion a
  // permission error on the VM caused, with no error anywhere in the UI.
  mutationCache: new MutationCache({
    onError: (error) => {
      if (error instanceof StoreVersionConflictError) {
        toast.error(error.message, { action: { label: 'Reload', onClick: () => window.location.reload() } })
        return
      }
      toast.error(error instanceof Error ? error.message : 'Something went wrong')
    },
  }),
})

// Needs to render *inside* QueryClientProvider to call useAccountingStore,
// so it can't live in App itself — App is the component that creates that
// provider, one level above where its own context becomes available.
function AppShell() {
  const { data: store, isPending: storeIsPending, isError: storeIsError } = useAccountingStore()
  const hasAnyData = hasAnyRealAccount(Object.values(store?.accounts ?? {}))
  useSyncBrowserTimezone()

  // The whole Money side reads from the same underlying data layer — if
  // the core store call is failing outright (e.g. the deploy VM's `data/`
  // bind mount got recreated root-owned by Docker, unreadable by the
  // non-root `appuser` the container actually runs as, until a one-time
  // `chown` fixes it), every one of these pages would too, each in its
  // own slightly different way, some of them blank rather than erroring.
  // One fallback here, instead of trusting every page's own error
  // handling to catch it.
  const moneyPage = (page: ReactNode) => (storeIsError ? <PageErrorFallback /> : page)

  return (
    <div className="flex h-screen bg-white">
      <Sidebar />
      <ErrorBoundary>
        <Routes>
          {routes.map(({ path, Component, requiresStore }) => (
            <Route key={path} path={path} element={requiresStore ? moneyPage(<Component />) : <Component />} />
          ))}
        </Routes>
      </ErrorBoundary>
      <OnboardingModal hasAnyData={hasAnyData} isLoading={storeIsPending} />
      <Toaster position="bottom-right" richColors />
    </div>
  )
}

export default function App() {
  return (
    <AuthGate>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delay={200}>
          <AppShell />
        </TooltipProvider>
      </QueryClientProvider>
    </AuthGate>
  )
}
