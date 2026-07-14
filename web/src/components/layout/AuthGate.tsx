import { useAuth } from '@clerk/react'
import { Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { ACTIVE_LANDING_PAGE, LANDING_PAGES } from '@/components/layout/landing-pages'

// The one gate everyone passes through before the app shell mounts — see
// AppShell in App.tsx for what's on the other side. Nothing beyond this
// component ever renders (no sidebar, no routes, no data fetch) until
// Clerk confirms a signed-in session, so there's no flash of real
// content while auth is still resolving or before sign-in completes.
// A link to this app always resolves to the same page either way — the
// active landing page while signed out, never Clerk's bare sign-in form
// on its own. Swap ACTIVE_LANDING_PAGE in landing-pages/index.ts to change it.
export function AuthGate({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth()
  const LandingPage = LANDING_PAGES[ACTIVE_LANDING_PAGE]

  if (!isLoaded) return <LoadingScreen />
  if (!isSignedIn) return <LandingPage />

  return <>{children}</>
}

function LoadingScreen() {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-3 bg-white">
      <span className="text-sm font-semibold tracking-tight text-foreground">Finance Dashboard</span>
      <Loader2 className="size-4 animate-spin text-muted-foreground/50" />
    </div>
  )
}
