import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface ModalProps {
  open: boolean
  title: string
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  /** Prevent closing via Escape/backdrop while a request is in flight. */
  dismissible?: boolean
}

export function Modal({ open, title, description, onClose, children, dismissible = true }: ModalProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    // Focus the first form control, falling back to the panel itself.
    const target = panelRef.current?.querySelector<HTMLElement>('input, textarea, select, button')
    ;(target ?? panelRef.current)?.focus()

    // Make the page behind the dialog inert: unreachable by Tab, clicks and screen readers.
    // The dialog is portalled to <body>, outside #root, so it stays interactive.
    const appRoot = document.getElementById('root')
    appRoot?.setAttribute('inert', '')

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dismissible) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      appRoot?.removeAttribute('inert')
      previouslyFocused?.focus()
    }
  }, [open, dismissible, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
      <div
        aria-hidden
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]"
        onClick={() => dismissible && onClose()}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl outline-none dark:border-slate-800 dark:bg-slate-900"
      >
        <h2 id={titleId} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        {description && <div className="mt-1.5 text-sm text-slate-600 dark:text-slate-400">{description}</div>}
        <div className="mt-5">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
