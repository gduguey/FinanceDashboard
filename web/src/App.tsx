import type { ReactNode } from 'react'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { Toaster, toast } from 'sonner'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { OnboardingModal } from '@/components/layout/OnboardingModal'
import { Sidebar } from '@/components/layout/Sidebar'
import { PageErrorFallback } from '@/components/shared/PageErrorFallback'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAccountingStore } from '@/hooks/useAccountingData'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import { AccountsPage } from '@/pages/AccountsPage'
import { AllocationPage } from '@/pages/AllocationPage'
import { BudgetPage } from '@/pages/BudgetPage'
import { CategoriesPage } from '@/pages/CategoriesPage'
import { GlossaryPage } from '@/pages/GlossaryPage'
import { GoalsPage } from '@/pages/GoalsPage'
import { GuidePage } from '@/pages/GuidePage'
import { ImportPage } from '@/pages/ImportPage'
import { InsightsPage } from '@/pages/InsightsPage'
import { NetWorthPage } from '@/pages/NetWorthPage'
import { OnboardingPage } from '@/pages/OnboardingPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { PerformancePage } from '@/pages/PerformancePage'
import { RulesPage } from '@/pages/RulesPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { SimulatorPage } from '@/pages/SimulatorPage'
import { TagsPage } from '@/pages/TagsPage'
import { TaxesPage } from '@/pages/TaxesPage'
import { TransactionsPage } from '@/pages/TransactionsPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  // One place, not one per button: every `useMutation` in the app
  // surfaces its failure here automatically, unless it opts out with its
  // own `onError`. Without this, a failed save just silently doesn't
  // happen — the exact "bad key still shows Connected" confusion a
  // permission error on the VM caused, with no error anywhere in the UI.
  mutationCache: new MutationCache({
    onError: (error) => toast.error(error instanceof Error ? error.message : 'Something went wrong'),
  }),
})

// Needs to render *inside* QueryClientProvider to call useAccountingStore,
// so it can't live in App itself — App is the component that creates that
// provider, one level above where its own context becomes available.
function AppShell() {
  const { data: store, isPending: storeIsPending, isError: storeIsError } = useAccountingStore()
  const hasAnyData = hasAnyRealAccount(Object.values(store?.accounts ?? {}))

  // The whole Money side reads from the same underlying data layer (see
  // docs/server-setup/storage-and-volumes.md's bind-mount permission
  // gotcha) — if the core store call is failing outright, every one of
  // these pages would too, each in its own slightly different way, some
  // of them blank rather than erroring. One fallback here, instead of
  // trusting every page's own error handling to catch it.
  const moneyPage = (page: ReactNode) => (storeIsError ? <PageErrorFallback /> : page)

  return (
    <div className="flex h-screen bg-white">
      <Sidebar />
      <ErrorBoundary>
        <Routes>
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/" element={moneyPage(<OverviewPage />)} />
          <Route path="/net-worth" element={moneyPage(<NetWorthPage />)} />
          <Route path="/insights" element={moneyPage(<InsightsPage />)} />
          <Route path="/transactions" element={moneyPage(<TransactionsPage />)} />
          <Route path="/accounts" element={moneyPage(<AccountsPage />)} />
          <Route path="/import" element={moneyPage(<ImportPage />)} />
          <Route path="/budget" element={moneyPage(<BudgetPage />)} />
          <Route path="/goals" element={moneyPage(<GoalsPage />)} />
          <Route path="/simulator" element={moneyPage(<SimulatorPage />)} />
          <Route path="/categories" element={moneyPage(<CategoriesPage />)} />
          <Route path="/tags" element={moneyPage(<TagsPage />)} />
          <Route path="/rules" element={moneyPage(<RulesPage />)} />
          <Route path="/investments" element={<PerformancePage />} />
          <Route path="/investments/allocation" element={<AllocationPage />} />
          <Route path="/investments/taxes" element={<TaxesPage />} />
          <Route path="/investments/glossary" element={<GlossaryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/guide" element={<GuidePage />} />
        </Routes>
      </ErrorBoundary>
      <OnboardingModal hasAnyData={hasAnyData} isLoading={storeIsPending} />
      <Toaster position="bottom-right" richColors />
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={200}>
        <AppShell />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
