import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { ToastContext, type ToastApi } from '../../contexts/toastContext'

type ToastTone = 'success' | 'error' | 'info'

interface Toast {
  id: number
  tone: ToastTone
  message: string
}

const DISMISS_AFTER_MS = 5000
const MAX_VISIBLE = 3

const TONES: Record<ToastTone, { ring: string; icon: string; iconClass: string }> = {
  success: {
    ring: 'border-emerald-200 dark:border-emerald-500/30',
    icon: 'M5 12l5 5L20 7',
    iconClass: 'text-emerald-600 dark:text-emerald-400',
  },
  error: {
    ring: 'border-red-200 dark:border-red-500/30',
    icon: 'M12 8v5m0 3h.01M10.3 3.9L2.5 17.5A2 2 0 004.2 20.5h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
    iconClass: 'text-red-600 dark:text-red-400',
  },
  info: {
    ring: 'border-slate-200 dark:border-slate-700',
    icon: 'M12 16v-4m0-4h.01M12 21a9 9 0 100-18 9 9 0 000 18z',
    iconClass: 'text-brand-600 dark:text-brand-300',
  },
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const tone = TONES[toast.tone]
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const start = useCallback(() => {
    timer.current = setTimeout(() => onDismiss(toast.id), DISMISS_AFTER_MS)
  }, [onDismiss, toast.id])
  const pause = () => clearTimeout(timer.current)

  useEffect(() => {
    start()
    return () => clearTimeout(timer.current)
  }, [start])

  return (
    <div
      // Errors interrupt (assertive); confirmations wait for a pause in speech (polite).
      role={toast.tone === 'error' ? 'alert' : 'status'}
      onMouseEnter={pause}
      onMouseLeave={start}
      onFocus={pause}
      onBlur={start}
      className={`pointer-events-auto flex w-full items-start gap-3 rounded-xl border bg-white p-3 text-sm shadow-lg motion-safe:animate-toast-in dark:bg-slate-900 ${tone.ring}`}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={`mt-0.5 size-4 shrink-0 ${tone.iconClass}`} aria-hidden>
        <path d={tone.icon} />
      </svg>
      <p className="flex-1 text-slate-800 dark:text-slate-100">{toast.message}</p>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        className="-m-1 rounded p-1 text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
        aria-label="Dismiss notification"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" className="size-3.5" aria-hidden>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>
  )
}

/** Short confirmations after an action ("Knowledge base deleted"). Errors that need the user's
 * attention in context still belong next to the thing that failed; a toast is for outcomes. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)

  const dismiss = useCallback((id: number) => setToasts((current) => current.filter((t) => t.id !== id)), [])
  const push = useCallback((tone: ToastTone, message: string) => {
    const id = ++nextId.current
    setToasts((current) => [...current, { id, tone, message }].slice(-MAX_VISIBLE))
  }, [])

  const api = useMemo<ToastApi>(
    () => ({
      success: (message) => push('success', message),
      error: (message) => push('error', message),
      info: (message) => push('info', message),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* No landmark role: each notification is its own status/alert live region. */}
      <div
        className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-96"
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}
