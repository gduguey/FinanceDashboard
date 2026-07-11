import { useAccountingStore, usePostings } from '@/hooks/useAccountingData'
import { useIbkrConnectionStatus } from '@/hooks/usePortfolioData'
import { hasAnyRealAccount } from '@/lib/postingClassification'

// The one shared definition of onboarding progress — the sidebar (whether
// to show/hide the Onboarding nav item) and the Onboarding page itself
// (which steps show green) both read this, so they can't disagree.
// Deliberately a stricter, two-part condition than the welcome *popup*
// uses (account only) — this is "done", the popup is just "you've taken
// the first step".
export function useOnboardingProgress() {
  const { data: store } = useAccountingStore()
  const { data: postings } = usePostings()
  const ibkr = useIbkrConnectionStatus()

  const hasAccount = hasAnyRealAccount(Object.values(store?.accounts ?? {}))
  const hasData = Boolean(postings && postings.length > 0)
  const ibkrConnected = ibkr.state === 'connected'

  return { hasAccount, hasData, ibkrConnected, isComplete: hasAccount && hasData }
}
