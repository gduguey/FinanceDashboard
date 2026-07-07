import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { Sidebar } from '@/components/layout/Sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AccountingPage } from '@/pages/AccountingPage'
import { BudgetPage } from '@/pages/BudgetPage'
import { GoalsPage } from '@/pages/GoalsPage'
import { GuidePage } from '@/pages/GuidePage'
import { ImportPage } from '@/pages/ImportPage'
import { InvestmentsPage } from '@/pages/InvestmentsPage'
import { NetWorthPage } from '@/pages/NetWorthPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { SimulatorPage } from '@/pages/SimulatorPage'

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
              <Route path="/investments" element={<InvestmentsPage />} />
              <Route path="/net-worth" element={<NetWorthPage />} />
              <Route path="/accounting" element={<AccountingPage />} />
              <Route path="/budget" element={<BudgetPage />} />
              <Route path="/goals" element={<GoalsPage />} />
              <Route path="/import" element={<ImportPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/simulator" element={<SimulatorPage />} />
              <Route path="/guide" element={<GuidePage />} />
            </Routes>
          </ErrorBoundary>
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
