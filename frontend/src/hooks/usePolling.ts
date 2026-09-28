import { useEffect, useRef } from 'react'

/**
 * Run `callback` every `intervalMs` while `active` is true. Uses chained
 * timeouts rather than setInterval so a slow request never overlaps the next
 * one. Errors are swallowed: polling is best-effort and resumes next tick.
 */
export function usePolling(callback: () => Promise<unknown>, active: boolean, intervalMs: number): void {
  const callbackRef = useRef(callback)

  useEffect(() => {
    callbackRef.current = callback
  }, [callback])

  useEffect(() => {
    if (!active) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>

    const tick = async () => {
      try {
        await callbackRef.current()
      } catch {
        // transient failure; try again on the next tick
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs)
    }
    timer = setTimeout(tick, intervalMs)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [active, intervalMs])
}
