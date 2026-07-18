import { Avatar as AvatarPrimitive } from '@base-ui/react/avatar'
import { useClerk, useUser } from '@clerk/react'
import { ChevronsUpDown, LogOut, UserCog } from 'lucide-react'
import { Truncate } from '@/components/shared/Truncate'
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from '@/components/ui/menu'

// Bottom-of-sidebar row instead of Clerk's own floating `<UserButton />` —
// built from useUser/useClerk rather than restyling UserButton's popover,
// since that popover's internal layout isn't part of Clerk's documented,
// stable appearance API the way `<SignIn>`/`<UserProfile>`'s own theme is
// (see `main.tsx`'s shadcn theme, which those two *do* pick up).
export function SidebarAccountMenu() {
  const { user } = useUser()
  const { openUserProfile, signOut } = useClerk()

  if (!user) return null

  const name = user.fullName ?? user.primaryEmailAddress?.emailAddress ?? 'Account'
  const email = user.primaryEmailAddress?.emailAddress

  return (
    <Menu>
      <MenuTrigger
        className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-muted-foreground/70 outline-none hover:bg-muted/50 hover:text-foreground data-popup-open:bg-muted/50 data-popup-open:text-foreground"
        render={<button type="button" />}
      >
        <AvatarPrimitive.Root className="flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted">
          <AvatarPrimitive.Image src={user.imageUrl} alt={name} className="size-full object-cover" />
          <AvatarPrimitive.Fallback className="text-xs font-semibold">
            {name.slice(0, 1).toUpperCase()}
          </AvatarPrimitive.Fallback>
        </AvatarPrimitive.Root>
        <span className="flex min-w-0 flex-1 flex-col">
          <Truncate text={name} className="text-sm font-semibold text-foreground" />
          {email && <Truncate text={email} className="text-xs text-muted-foreground/70" />}
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground/50" />
      </MenuTrigger>
      <MenuContent side="top" align="start" className="min-w-(--anchor-width)">
        <MenuItem onClick={() => openUserProfile()}>
          <UserCog className="size-4" />
          Manage account
        </MenuItem>
        <MenuSeparator />
        <MenuItem onClick={() => void signOut()}>
          <LogOut className="size-4" />
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  )
}
