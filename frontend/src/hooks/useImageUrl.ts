import { useEffect, useState } from 'react'

import { fetchImageBlob } from '../services/images'

type State = { url: string | null; failed: boolean }

/** Object URL for a stored image, fetched with the session token; revoked on unmount. */
export function useImageUrl(imageId: string): State {
  const [state, setState] = useState<State>({ url: null, failed: false })

  useEffect(() => {
    let url: string | null = null
    let cancelled = false
    fetchImageBlob(imageId)
      .then((blob) => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setState({ url, failed: false })
      })
      .catch(() => {
        if (!cancelled) setState({ url: null, failed: true })
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [imageId])

  return state
}
