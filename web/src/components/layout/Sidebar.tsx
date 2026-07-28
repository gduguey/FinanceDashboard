import {
  ArrowRightLeft,
  BarChart3,
  BookOpen,
  CircleCheckBig,
  FlaskConical,
  Home,
  Landmark,
  Library,
  LineChart,
  Percent,
  PieChart,
  Receipt,
  Scale,
  Settings,
  Shapes,
  Tag,
  Target,
  Upload,
  Wallet,
} from 'lucide-react'
import { type ComponentType, useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { SidebarAccountMenu } from '@/components/layout/SidebarAccountMenu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useOnboardingProgress } from '@/hooks/useOnboardingProgress'
import { useIbkrConnectionStatus } from '@/hooks/usePortfolioData'
import { cn } from '@/lib/utils'

interface NavItem {
  label: string
  icon: ComponentType<{ className?: string }>
  path: string
}

// Never change side depending on which mode is selected below — they
// answer "I need this regardless," not "which side am I on."
const PINNED_ITEMS: NavItem[] = [
  { label: 'Overview', icon: Home, path: '/' },
  { label: 'Net Worth', icon: Scale, path: '/net-worth' },
]

// Everyday things you touch often: reviewing what happened, and getting
// new statements in. Import sits at the end of this group (not up in
// Setup) since importing is itself an everyday task, not a one-time setup step.
const EVERYDAY_ITEMS: NavItem[] = [
  { label: 'Insights', icon: BarChart3, path: '/insights' },
  { label: 'Transactions', icon: Receipt, path: '/transactions' },
  { label: 'Accounts', icon: Landmark, path: '/accounts' },
  { label: 'Import', icon: Upload, path: '/import' },
]

// Forward-looking, lower-frequency views — you don't check these every
// day the way you check Transactions, but they're still everyday-side
// concerns, not one-time configuration.
const PLANNING_ITEMS: NavItem[] = [
  { label: 'Budget', icon: Wallet, path: '/budget' },
  { label: 'Goals', icon: Target, path: '/goals' },
  { label: 'Simulator', icon: FlaskConical, path: '/simulator' },
]

// Configure-occasionally taxonomy and automation — set up once, revisited
// rarely, never the first thing you reach for on a normal day.
const SETUP_ITEMS: NavItem[] = [
  { label: 'Categories', icon: Shapes, path: '/categories' },
  { label: 'Tags', icon: Tag, path: '/tags' },
  { label: 'Rules', icon: ArrowRightLeft, path: '/rules' },
]

// Secondary, utility-style actions — data entry, documentation, and
// connection setup rather than a page of its own data — pinned to the
// very bottom of the sidebar, separate from the primary section list above.
const BOTTOM_ITEMS: NavItem[] = [
  { label: 'Settings', icon: Settings, path: '/settings' },
  { label: 'Guide', icon: BookOpen, path: '/guide' },
]

// At only a handful of items there's no group worth naming, per the
// mock's own caption on the Money side's larger tree.
const INVESTMENTS_ITEMS: NavItem[] = [
  { label: 'Performance', icon: LineChart, path: '/investments' },
  { label: 'Allocation', icon: PieChart, path: '/investments/allocation' },
  { label: 'Taxes', icon: Percent, path: '/investments/taxes' },
  { label: 'Glossary', icon: Library, path: '/investments/glossary' },
]

const ITEM_CLASSES =
  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted-foreground/70 hover:bg-muted/50 hover:text-foreground'
const ACTIVE_ITEM_CLASSES = 'bg-white font-semibold text-foreground shadow-[0_0_0_1px_var(--border)] hover:bg-white'
const GROUP_LABEL_CLASSES = 'px-3 pt-3 pb-1 text-[11px] font-bold tracking-wide text-muted-foreground/70 uppercase'

function NavLinks({ items }: { items: NavItem[] }) {
  return (
    <>
      {items.map(({ label, icon: Icon, path }) => (
        <NavLink
          key={label}
          to={path}
          // `/investments` is itself a prefix of `/investments/allocation`,
          // `/investments/taxes`, and `/investments/glossary` — end-match
          // it (like the root path) so Performance doesn't show active
          // while viewing one of its siblings.
          end={path === '/' || path === '/investments'}
          className={({ isActive }) => cn(ITEM_CLASSES, isActive && ACTIVE_ITEM_CLASSES)}
        >
          <Icon className="size-4" />
          {label}
        </NavLink>
      ))}
    </>
  )
}

// The Money/Investments switch — flipping it swaps everything below for
// that side's own nav (Everyday/Planning/Setup vs. Performance/Allocation/
// Taxes/Glossary), while Overview, Net Worth, Settings, and Guide stay put
// above and below it since they aren't scoped to either side.
function MoneyInvestmentsSwitch({ mode }: { mode: 'money' | 'investments' }) {
  // `connected`, not just `configured` — a bad token still counts as
  // "something's typed in", but shouldn't unlock a page that has nothing
  // real to show once IBKR actually rejects it.
  const { state } = useIbkrConnectionStatus()
  const ibkrConnected = state === 'connected'

  return (
    <div className="my-2 flex rounded-lg border border-border bg-muted/40 p-0.5">
      <Link
        to="/insights"
        className={cn(
          'flex-1 rounded-md py-1 text-center text-xs font-semibold',
          mode === 'money' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
        )}
      >
        Money
      </Link>
      {ibkrConnected ? (
        <Link
          to="/investments"
          className={cn(
            'flex-1 rounded-md py-1 text-center text-xs font-semibold',
            mode === 'investments'
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          Investments
        </Link>
      ) : (
        <Tooltip>
          <TooltipTrigger
            render={
              <Link
                to="/settings"
                className="flex-1 rounded-md py-1 text-center text-xs font-semibold text-muted-foreground/40 hover:text-muted-foreground/70"
              />
            }
          >
            Investments
          </TooltipTrigger>
          <TooltipContent className="max-w-56 text-pretty">
            Only works with IBKR for now — set up your key in Settings to unlock.
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

// Every path that unambiguously belongs to the Money side — used to
// resolve which mode the switch should show. Anything not in this set and
// not under `/investments` (Overview, Net Worth, Settings, Guide) is
// mode-agnostic: visiting it must never flip the switch away from
// whichever side the user was already on, since none of the money/
// investments group is even shown on those pages.
const MONEY_PATHS = new Set([...EVERYDAY_ITEMS, ...PLANNING_ITEMS, ...SETUP_ITEMS].map((item) => item.path))

function useSidebarMode(): 'money' | 'investments' {
  const location = useLocation()
  const [mode, setMode] = useState<'money' | 'investments'>(
    location.pathname.startsWith('/investments') ? 'investments' : 'money',
  )
  useEffect(() => {
    if (location.pathname.startsWith('/investments')) setMode('investments')
    else if (MONEY_PATHS.has(location.pathname)) setMode('money')
  }, [location.pathname])
  return mode
}

// Shown above Overview only while there's still something to do — an
// account and real data both — and gone for good once both exist,
// matching useOnboardingProgress's stricter rule (the welcome *popup*
// only checks for an account; this checks for both, since it's a
// standing nav item, not a one-time nudge).
function OnboardingNavItem() {
  const { isComplete } = useOnboardingProgress()
  if (isComplete) return null

  return (
    <NavLink
      to="/onboarding"
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2.5 rounded-lg border border-emerald-600/30 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-100',
          isActive && 'border-emerald-600 bg-emerald-100',
        )
      }
    >
      <CircleCheckBig className="size-4" />
      Onboarding
    </NavLink>
  )
}

export function Sidebar() {
  const mode = useSidebarMode()

  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-border bg-white">
      <div className="px-5 py-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">Finance Dashboard</span>
      </div>
      <nav className="flex flex-col gap-0.5 px-3">
        <OnboardingNavItem />
        <NavLinks items={PINNED_ITEMS} />
      </nav>

      <div className="mt-1 flex-1 overflow-y-auto px-3">
        <MoneyInvestmentsSwitch mode={mode} />

        {mode === 'money' ? (
          <nav className="flex flex-col gap-0.5">
            <div className={GROUP_LABEL_CLASSES}>Everyday</div>
            <NavLinks items={EVERYDAY_ITEMS} />
            <div className={GROUP_LABEL_CLASSES}>Planning</div>
            <NavLinks items={PLANNING_ITEMS} />
            <div className={GROUP_LABEL_CLASSES}>Setup</div>
            <NavLinks items={SETUP_ITEMS} />
          </nav>
        ) : (
          <nav className="flex flex-col gap-0.5">
            <NavLinks items={INVESTMENTS_ITEMS} />
          </nav>
        )}
      </div>

      <nav className="flex flex-col gap-0.5 border-t border-border px-3 py-3">
        <NavLinks items={BOTTOM_ITEMS} />
      </nav>

      <div className="border-t border-border p-2">
        <SidebarAccountMenu />
      </div>
    </aside>
  )
}
