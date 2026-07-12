import { useAuth } from '@clerk/react'
import { Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { LandingPage } from '@/components/layout/LandingPage'

// The one gate everyone passes through before the app shell mounts — see
// AppShell in App.tsx for what's on the other side. Nothing beyond this
// component ever renders (no sidebar, no routes, no data fetch) until
// Clerk confirms a signed-in session, so there's no flash of real
// content while auth is still resolving or before sign-in completes.
// A link to this app always resolves to the same page either way —
// LandingPage while signed out, never Clerk's bare sign-in form on its own.
export function AuthGate({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth()

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
