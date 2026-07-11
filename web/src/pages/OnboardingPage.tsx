import { ArrowRight, Check } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '@/components/layout/PageHeader'
import { buttonVariants } from '@/components/ui/button'
import { useOnboardingProgress } from '@/hooks/useOnboardingProgress'
import { cn } from '@/lib/utils'

interface Step {
  label: string
  description: string
  cta: string
  to: string
  optional?: boolean
  done: boolean
}

// This page disappears from the sidebar once `isComplete` (see
// useOnboardingProgress) — visiting it directly afterward still works and
// just shows all-green, rather than redirecting somewhere, since that's
// harmless and simpler than adding a special case for "you're already done".
export function OnboardingPage() {
  const { hasAccount, hasData, ibkrConnected } = useOnboardingProgress()

  const steps: Step[] = [
    {
      label: 'Create an account',
      description: 'A checking account, a card, a brokerage — whatever you want to track.',
      cta: 'Go to Accounts',
      to: '/accounts',
      done: hasAccount,
    },
    {
      label: 'Import your data',
      description: 'Upload a statement (CSV, PDF, or Excel) for that account to start seeing real numbers.',
      cta: 'Go to Import',
      to: '/import',
      done: hasData,
    },
    {
      label: 'Connect IBKR',
      description: 'Optional — unlocks the Investments side of the app. Can be done anytime.',
      cta: 'Go to Settings',
      to: '/settings',
      optional: true,
      done: ibkrConnected,
    },
  ]

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Onboarding" />
      <div className="mx-auto max-w-lg px-8 py-8">
        <ol className="space-y-6">
          {steps.map((step, index) => (
            <li key={step.label} className="relative flex gap-4 pl-1">
              {index < steps.length - 1 && (
                <span className="absolute top-7 left-[15px] h-[calc(100%+0.5rem)] w-px bg-border" aria-hidden />
              )}
              <span
                className={cn(
                  'z-10 flex size-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold',
                  step.done
                    ? 'border-emerald-600 bg-emerald-600 text-white'
                    : 'border-border bg-white text-muted-foreground',
                )}
              >
                {step.done ? <Check className="size-4" /> : index + 1}
              </span>
              <div className="flex-1 pt-1">
                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  {step.label}
                  {step.optional && (
                    <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                      optional
                    </span>
                  )}
                  {step.done && <span className="text-xs font-normal text-emerald-600">Done</span>}
                </div>
                <p className="mt-0.5 text-sm text-muted-foreground">{step.description}</p>
                {!step.done && (
                  <Link to={step.to} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'mt-2')}>
                    {step.cta} <ArrowRight className="size-3.5" />
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
