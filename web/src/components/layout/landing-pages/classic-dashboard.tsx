import { SignInButton } from '@clerk/react'
import { LineChart, ShieldCheck, Wallet } from 'lucide-react'
import { Button } from '@/components/ui/button'

// The original, sober landing page — kept around as a fallback / for when
// no themed variant is running. See landing-pages/index.ts to switch back.
export function ClassicDashboardLanding() {
  return (
    <div className="flex min-h-screen flex-col bg-white">
      <nav className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="size-5" />
            <span className="text-sm font-semibold tracking-tight text-foreground">Finance Dashboard</span>
          </div>
          <SignInButton mode="modal">
            <Button variant="outline" size="sm">
              Sign in
            </Button>
          </SignInButton>
        </div>
      </nav>

      <main className="flex flex-1 items-center justify-center px-6">
        <div className="flex max-w-lg flex-col items-center gap-8 py-20 text-center">
          <div className="flex flex-col items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight text-foreground">Finance Dashboard</h1>
            <p className="text-pretty text-sm text-muted-foreground">
              Net worth, budgets, goals, and investment performance — replayed from your own accounts and broker
              statements, in one private dashboard.
            </p>
          </div>

          <SignInButton mode="modal">
            <Button size="lg">Sign in to continue</Button>
          </SignInButton>

          <dl className="grid grid-cols-1 gap-4 text-left sm:grid-cols-3">
            <div className="flex flex-col items-center gap-1.5 text-center">
              <Wallet className="size-4 text-muted-foreground/70" />
              <dt className="text-xs font-semibold text-foreground">Budgets & goals</dt>
            </div>
            <div className="flex flex-col items-center gap-1.5 text-center">
              <LineChart className="size-4 text-muted-foreground/70" />
              <dt className="text-xs font-semibold text-foreground">Investment performance</dt>
            </div>
            <div className="flex flex-col items-center gap-1.5 text-center">
              <ShieldCheck className="size-4 text-muted-foreground/70" />
              <dt className="text-xs font-semibold text-foreground">Private, invite-only</dt>
            </div>
          </dl>
        </div>
      </main>

      <footer className="border-t border-border px-6 py-4 text-center text-xs text-muted-foreground/70">
        Personal finance dashboard — private, by invitation only.
      </footer>
    </div>
  )
}
