import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Changing this (e.g. the current path) clears the error, so navigating away recovers. */
  resetKey?: string
}

interface State {
  error: Error | null
  resetKey?: string
}

/**
 * Catches errors thrown while rendering, so a bug in one page shows a recovery screen
 * instead of a blank window. Errors from event handlers and requests are handled where
 * they happen (ApiError messages); this is the last line of defence.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    // A new resetKey (the user navigated) clears the error before the next render.
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The console is the only place the details go; the user sees a plain message.
    console.error('Unhandled rendering error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="mx-auto max-w-lg px-4 py-16 text-center">
        <h1 className="text-xl font-semibold tracking-tight">Something went wrong</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          This page hit an unexpected problem. Your data is safe; reload the page or go back to the dashboard.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Reload page
          </button>
          <a
            href="/"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            Go to dashboard
          </a>
        </div>
      </div>
    )
  }
}
