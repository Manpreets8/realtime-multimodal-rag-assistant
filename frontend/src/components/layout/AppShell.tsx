import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'

import { useAuth } from '../../hooks/useAuth'
import { Logo } from './Logo'

interface NavItem {
  label: string
  to: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: 'M3 12l9-9 9 9M5 10v10h14V10' },
  { label: 'Knowledge bases', to: '/knowledge-bases', icon: 'M4 6h16M4 12h16M4 18h10' },
  { label: 'Chat', to: '/chat', icon: 'M4 5h16v11H8l-4 4z' },
]

const ADMIN_ITEM: NavItem = {
  label: 'Admin',
  to: '/admin',
  icon: 'M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z',
}

function NavIcon({ path }: { path: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-4.5"
      aria-hidden
    >
      <path d={path} />
    </svg>
  )
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth()
  const items = user?.role === 'admin' ? [...NAV_ITEMS, ADMIN_ITEM] : NAV_ITEMS
  return (
    <nav className="flex flex-col gap-1" aria-label="Main">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-100'
                : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'
            }`
          }
        >
          <NavIcon path={item.icon} />
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

function UserMenu() {
  const { user, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)
  const name = user?.full_name || user?.email || ''
  const initials = name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('')

  return (
    <div className="flex items-center gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-500/20 dark:text-brand-100">
        {initials}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{user?.full_name || 'Account'}</p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user?.email}</p>
      </div>
      <button
        type="button"
        onClick={() => {
          setLoggingOut(true)
          void logout()
        }}
        disabled={loggingOut}
        className="rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50 dark:hover:bg-slate-800 dark:hover:text-slate-200 dark:text-slate-400"
      >
        Log out
      </button>
    </div>
  )
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-svh lg:flex">
      {/* Mobile top bar */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden dark:border-slate-800 dark:bg-slate-900">
        <Logo />
        <button
          type="button"
          onClick={() => setMobileOpen((open) => !open)}
          aria-expanded={mobileOpen}
          aria-label="Toggle navigation"
          className="rounded-md p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden>
            <path d={mobileOpen ? 'M6 6l12 12M18 6L6 18' : 'M4 7h16M4 12h16M4 17h16'} strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <aside
        className={`${mobileOpen ? 'flex' : 'hidden'} flex-col gap-6 border-b border-slate-200 bg-white p-4 lg:sticky lg:top-0 lg:flex lg:h-svh lg:w-64 lg:shrink-0 lg:border-r lg:border-b-0 dark:border-slate-800 dark:bg-slate-900`}
      >
        <div className="hidden px-2 pt-1 lg:block">
          <Logo />
        </div>
        <div className="flex-1">
          <Navigation onNavigate={() => setMobileOpen(false)} />
        </div>
        <UserMenu />
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
