import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../services/api'
import { getLiveness, getReadiness, type Liveness, type Readiness } from '../services/health'

export interface SystemStatus {
  liveness: Liveness | null
  readiness: Readiness | null
  error: ApiError | null
  loading: boolean
  refresh: () => void
}

export function useSystemStatus(): SystemStatus {
  const [liveness, setLiveness] = useState<Liveness | null>(null)
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false

    Promise.all([getLiveness(), getReadiness()])
      .then(([live, ready]) => {
        if (cancelled) return
        setLiveness(live)
        setReadiness(ready)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLiveness(null)
        setReadiness(null)
        setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Unexpected error.', null))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [attempt])

  const refresh = useCallback(() => {
    setLoading(true)
    setAttempt((n) => n + 1)
  }, [])

  return { liveness, readiness, error, loading, refresh }
}
