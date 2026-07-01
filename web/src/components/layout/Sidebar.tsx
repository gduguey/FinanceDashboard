import { Calculator, LineChart, Wallet } from 'lucide-react'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { label: 'Investments', icon: LineChart, active: true },
  { label: 'Budget', icon: Wallet, active: false },
  { label: 'Accounting', icon: Calculator, active: false },
]

export function Sidebar() {
  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-border bg-white">
      <div className="px-5 py-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">
          Finance Dashboard
        </span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3">
        {NAV_ITEMS.map(({ label, icon: Icon, active }) => (
          <div
            key={label}
            className={cn(
              'flex items-center justify-between rounded-lg px-3 py-2 text-sm',
              active
                ? 'bg-muted font-medium text-foreground'
                : 'text-muted-foreground/70 cursor-not-allowed',
            )}
          >
            <span className="flex items-center gap-2.5">
              <Icon className="size-4" />
              {label}
            </span>
            {!active && <span className="text-[10px] uppercase tracking-wide">Soon</span>}
          </div>
        ))}
      </nav>
    </aside>
  )
}
