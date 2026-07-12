import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Sidebar } from '@/components/layout/Sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AccountingPage } from '@/pages/AccountingPage'
import { BudgetPage } from '@/pages/BudgetPage'
import { ImportPage } from '@/pages/ImportPage'
import { InvestmentsPage } from '@/pages/InvestmentsPage'
import { NetWorthPage } from '@/pages/NetWorthPage'
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
          <Routes>
            <Route path="/" element={<Navigate to="/investments" replace />} />
            <Route path="/investments" element={<InvestmentsPage />} />
            <Route path="/net-worth" element={<NetWorthPage />} />
            <Route path="/accounting" element={<AccountingPage />} />
            <Route path="/budget" element={<BudgetPage />} />
            <Route path="/import" element={<ImportPage />} />
            <Route path="/simulator" element={<SimulatorPage />} />
          </Routes>
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
