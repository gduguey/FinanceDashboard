import { RefreshCw } from 'lucide-react'
import { LoadingProgressBar } from '@/components/shared/LoadingProgressBar'
import { Button } from '@/components/ui/button'
import { useSyncExchangeRates } from '@/hooks/useAccountingData'

// The one sync action shared by every page that shows a currency-converted
// total (the header currency toggle) and the Net Worth exchange-rate panel
// itself — same button, same progress feedback, wherever it appears.
export function ExchangeRateSyncButton() {
  const sync = useSyncExchangeRates()
  return (
    <div className="flex items-center gap-2">
      {sync.isPending && <LoadingProgressBar step="Fetching latest FX rates from the European Central Bank…" />}
      {sync.isError && <span className="text-xs text-destructive">{sync.error.message}</span>}
      <Button variant="outline" size="sm" onClick={() => sync.mutate()} disabled={sync.isPending}>
        <RefreshCw className={sync.isPending ? 'size-3.5 animate-spin' : 'size-3.5'} />
        {sync.isPending ? 'Syncing…' : 'Sync rates'}
      </Button>
    </div>
  )
}
