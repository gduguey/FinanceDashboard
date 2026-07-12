import { BookOpen, Calculator, FlaskConical, LineChart, Scale, Target, Upload, Wallet } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { label: 'Accounting', icon: Calculator, path: '/accounting' },
  { label: 'Investments', icon: LineChart, path: '/investments' },
  { label: 'Net Worth', icon: Scale, path: '/net-worth' },
  { label: 'Budget', icon: Wallet, path: '/budget' },
  { label: 'Goals', icon: Target, path: '/goals' },
  { label: 'Simulator', icon: FlaskConical, path: '/simulator' },
]

// Secondary, utility-style actions — data entry and documentation rather
// than a page of its own data — pinned to the very bottom of the sidebar
// the way an app typically anchors "Account"/"Settings" there, separate
// from the primary section list above.
const SECONDARY_NAV_ITEMS = [
  { label: 'Import', icon: Upload, path: '/import' },
  { label: 'Guide', icon: BookOpen, path: '/guide' },
]

function NavLinks({ items }: { items: typeof NAV_ITEMS }) {
  return (
    <>
      {items.map(({ label, icon: Icon, path }) => (
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
    </>
  )
}

export function Sidebar() {
  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-border bg-white">
      <div className="px-5 py-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">
          Finance Dashboard
        </span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3">
        <NavLinks items={NAV_ITEMS} />
      </nav>
      <nav className="mt-auto flex flex-col gap-0.5 border-t border-border px-3 py-3">
        <NavLinks items={SECONDARY_NAV_ITEMS} />
      </nav>
    </aside>
  )
}
