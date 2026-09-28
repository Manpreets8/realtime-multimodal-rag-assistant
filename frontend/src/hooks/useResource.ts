import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'

import { ApiError } from '../services/api'

export interface Resource<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  reload: () => void
  /** Local optimistic updates (e.g. after create/delete) without refetching. */
  setData: Dispatch<SetStateAction<T | null>>
}

/** Load data from an async fetcher; refetches when `fetcher` identity changes (wrap it in useCallback). */
export function useResource<T>(fetcher: () => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    fetcher()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [fetcher, attempt])

  const reload = useCallback(() => {
    setLoading(true)
    setAttempt((n) => n + 1)
  }, [])

  return { data, error, loading, reload, setData }
}
