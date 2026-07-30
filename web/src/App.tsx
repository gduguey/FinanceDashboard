import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Suspense, useEffect } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Toaster, toast } from 'sonner'
import { AuthGate } from '@/components/layout/AuthGate'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { OnboardingModal } from '@/components/layout/OnboardingModal'
import { RouteFallback } from '@/components/layout/RouteFallback'
import { Sidebar } from '@/components/layout/Sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAccountingStore } from '@/hooks/useAccountingData'
import { useSyncBrowserTimezone } from '@/hooks/usePortfolioData'
import { RowVersionConflictError } from '@/lib/api'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { routes } from '@/routeTable'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  // One place, not one per button: every `useMutation` in the app
  // surfaces its failure here automatically, unless it opts out with its
  // own `onError`. Without this, a failed save just silently doesn't
  // happen — the exact "bad key still shows Connected" confusion a
  // permission error on the VM caused, with no error anywhere in the UI.
  mutationCache: new MutationCache({
    onError: (error) => {
      if (error instanceof RowVersionConflictError) {
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
  const { data: store, isPending: storeIsPending } = useAccountingStore()
  const hasAnyData = hasAnyRealAccount(Object.values(store?.accounts ?? {}))
  useSyncBrowserTimezone()

  // `queryClient` is a module singleton that outlives AuthGate's sign-out
  // unmount, so without this the next signed-in user would be served the
  // previous user's cached finance data. Drop every cached query on sign-out
  // (when this shell unmounts), so each session starts clean.
  useEffect(() => () => queryClient.clear(), [])

  return (
    <div className="flex h-screen bg-white">
      <Sidebar />
      <ErrorBoundary>
        {/* Inside ErrorBoundary so a chunk that fails to load surfaces as the
            app's own error page, and below Sidebar so navigating never
            unmounts the nav — only the page area waits for its chunk. */}
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            {routes.map(({ path, Component }) => (
              <Route key={path} path={path} element={<Component />} />
            ))}
          </Routes>
        </Suspense>
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
