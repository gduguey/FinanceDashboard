import { ArrowRight, Check } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button, buttonVariants } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { usePersistedState } from '@/hooks/usePersistedState'
import { cn } from '@/lib/utils'

interface Step {
  label: string
  description: string
  cta: string
  to: string
  optional?: boolean
}

const STEPS: Step[] = [
  {
    label: 'Create an account',
    description: 'A checking account, a card, a brokerage — whatever you want to track.',
    cta: 'Go to Accounts',
    to: '/accounts',
  },
  {
    label: 'Import your data',
    description: 'Upload a statement (CSV, PDF, or Excel) for that account to start seeing real numbers.',
    cta: 'Go to Import',
    to: '/import',
  },
  {
    label: 'Connect IBKR',
    description: 'Optional — unlocks the Investments side of the app. Can be done anytime.',
    cta: 'Go to Settings',
    to: '/settings',
    optional: true,
  },
]

// Shown once, the first time there's nothing set up yet — a vertical
// timeline rather than a wall of text, since these three steps are
// sequential (step 3 makes no sense before 1-2 exist) and the visual
// makes that order obvious at a glance instead of requiring a re-read.
export function OnboardingModal({ hasAnyData }: { hasAnyData: boolean }) {
  const [dismissed, setDismissed] = usePersistedState('onboarding-dismissed', false)
  const open = !dismissed && !hasAnyData

  return (
    <Dialog open={open} onOpenChange={(next) => !next && setDismissed(true)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Welcome — let's get you set up</DialogTitle>
        </DialogHeader>
        <ol className="space-y-6 py-2">
          {STEPS.map((step, index) => (
            <li key={step.label} className="relative flex gap-4 pl-1">
              {index < STEPS.length - 1 && (
                <span className="absolute top-7 left-[15px] h-[calc(100%+0.5rem)] w-px bg-border" aria-hidden />
              )}
              <span className="z-10 flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-white text-xs font-semibold text-muted-foreground">
                {index + 1}
              </span>
              <div className="flex-1 pt-1">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  {step.label}
                  {step.optional && (
                    <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                      optional
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-sm text-muted-foreground">{step.description}</p>
                <Link
                  to={step.to}
                  onClick={() => setDismissed(true)}
                  className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'mt-2')}
                >
                  {step.cta} <ArrowRight className="size-3.5" />
                </Link>
              </div>
            </li>
          ))}
        </ol>
        <Button variant="ghost" size="sm" className="self-end text-muted-foreground" onClick={() => setDismissed(true)}>
          <Check className="size-3.5" />
          I'll figure it out
        </Button>
      </DialogContent>
    </Dialog>
  )
}
