import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Sidebar } from '@/components/layout/Sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { InvestmentsPage } from '@/pages/InvestmentsPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: false } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={200}>
        <div className="flex h-screen bg-white">
          <Sidebar />
          <InvestmentsPage />
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
