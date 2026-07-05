import { Calculator, FlaskConical, LineChart, Scale, Upload, Wallet } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { label: 'Investments', icon: LineChart, path: '/investments' },
  { label: 'Net Worth', icon: Scale, path: '/net-worth' },
  { label: 'Accounting', icon: Calculator, path: '/accounting' },
  { label: 'Budget', icon: Wallet, path: '/budget' },
  { label: 'Import', icon: Upload, path: '/import' },
  { label: 'Simulator', icon: FlaskConical, path: '/simulator' },
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
        {NAV_ITEMS.map(({ label, icon: Icon, path }) => (
          <NavLink
            key={label}
            to={path}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm',
                isActive
                  ? 'bg-muted font-medium text-foreground'
                  : 'text-muted-foreground/70 hover:bg-muted/50 hover:text-foreground',
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
