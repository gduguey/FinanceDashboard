import { SignIn, useAuth } from '@clerk/react'
import type { ReactNode } from 'react'

// The one gate everyone passes through before the app shell mounts — see
// AppShell in App.tsx for what's on the other side. Nothing beyond this
// component ever renders (no sidebar, no routes, no data fetch) until
// Clerk confirms a signed-in session, so there's no flash of real
// content while auth is still resolving or before sign-in completes.
export function AuthGate({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth()

  if (!isLoaded) return <GateScreen />
  if (!isSignedIn) return <GateScreen showSignIn />

  return <>{children}</>
}

function GateScreen({ showSignIn }: { showSignIn?: boolean }) {
  return (
    <div className="flex h-screen items-center justify-center bg-white">
      <div className="flex flex-col items-center gap-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">Finance Dashboard</span>
        {showSignIn && <SignIn routing="hash" />}
      </div>
    </div>
  )
}
