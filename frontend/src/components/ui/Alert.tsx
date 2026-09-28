import type { ReactNode } from 'react'

export function ErrorAlert({ children, requestId }: { children: ReactNode; requestId?: string | null }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300"
    >
      {children}
      {requestId && <span className="ml-1 font-mono text-xs opacity-70">(ref {requestId})</span>}
    </div>
  )
}
