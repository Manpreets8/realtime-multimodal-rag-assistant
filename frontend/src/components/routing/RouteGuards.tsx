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

/** Login/register pages: signed-in users are sent to the app instead. */
export function RedirectIfAuthenticated() {
  const { status } = useAuth()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <Outlet />
}
