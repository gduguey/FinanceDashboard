import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { OnboardingModal } from '@/components/layout/OnboardingModal'
import { Sidebar } from '@/components/layout/Sidebar'
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
})

// Needs to render *inside* QueryClientProvider to call useAccountingStore,
// so it can't live in App itself — App is the component that creates that
// provider, one level above where its own context becomes available.
function AppShell() {
  const { data: store } = useAccountingStore()
  const hasAnyData = hasAnyRealAccount(Object.values(store?.accounts ?? {}))

  return (
    <div className="flex h-screen bg-white">
      <Sidebar />
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/net-worth" element={<NetWorthPage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/budget" element={<BudgetPage />} />
          <Route path="/goals" element={<GoalsPage />} />
          <Route path="/simulator" element={<SimulatorPage />} />
          <Route path="/categories" element={<CategoriesPage />} />
          <Route path="/tags" element={<TagsPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/investments" element={<PerformancePage />} />
          <Route path="/investments/allocation" element={<AllocationPage />} />
          <Route path="/investments/taxes" element={<TaxesPage />} />
          <Route path="/investments/glossary" element={<GlossaryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/guide" element={<GuidePage />} />
        </Routes>
      </ErrorBoundary>
      <OnboardingModal hasAnyData={hasAnyData} />
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
