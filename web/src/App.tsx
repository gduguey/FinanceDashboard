import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { Sidebar } from '@/components/layout/Sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AccountsPage } from '@/pages/AccountsPage'
import { AllocationPage } from '@/pages/AllocationPage'
import { BudgetPage } from '@/pages/BudgetPage'
import { CategoriesPage } from '@/pages/CategoriesPage'
import { GoalsPage } from '@/pages/GoalsPage'
import { GuidePage } from '@/pages/GuidePage'
import { ImportPage } from '@/pages/ImportPage'
import { InsightsPage } from '@/pages/InsightsPage'
import { InvestmentsReferencePage } from '@/pages/InvestmentsReferencePage'
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

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={200}>
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
              <Route path="/investments/reference" element={<InvestmentsReferencePage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/guide" element={<GuidePage />} />
            </Routes>
          </ErrorBoundary>
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
