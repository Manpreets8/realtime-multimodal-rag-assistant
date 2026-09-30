import { Navigate, Outlet, useLocation } from 'react-router'

import { useAuth } from '../../hooks/useAuth'

function FullPageSpinner() {
  return (
    <div className="flex min-h-svh items-center justify-center" role="status" aria-label="Loading">
      <span className="size-6 animate-spin rounded-full border-2 border-brand-600 border-r-transparent" />
    </div>
  )
}

/** Renders child routes only for signed-in users; otherwise redirects to /login and remembers the target. */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location }} />
  return <Outlet />
}

/** Administrator pages. The API enforces this too; the guard only avoids showing a page that
 * would fail on every request. */
export function RequireAdmin() {
  const { user } = useAuth()

  if (user?.role !== 'admin') {
    return (
      <div className="mx-auto max-w-5xl px-4 py-16 text-center sm:px-8">
        <h1 className="text-xl font-semibold">Administrators only</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Your account does not have access to this page.
        </p>
      </div>
    )
  }
  return <Outlet />
}

/** Login/register pages: signed-in users are sent to the app instead. */
export function RedirectIfAuthenticated() {
  const { status } = useAuth()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <Outlet />
}
