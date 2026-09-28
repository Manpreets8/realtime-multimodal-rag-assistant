import { Link } from 'react-router'

export default function NotFoundPage() {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center px-4 text-center">
      <p className="text-sm font-semibold text-brand-600 dark:text-brand-300">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">The page you are looking for does not exist.</p>
      <Link to="/" className="mt-6 text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300">
        Go to dashboard
      </Link>
    </main>
  )
}
