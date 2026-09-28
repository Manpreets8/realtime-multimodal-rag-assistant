import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../services/api'
import { uploadDocument, type DocumentItem, type UploadConfig } from '../services/documents'
import { formatBytes } from '../utils/format'

export type UploadState = 'queued' | 'uploading' | 'error'

export interface UploadItem {
  id: string
  file: File
  state: UploadState
  progress: number
  error: string | null
}

/** Client-side pre-check mirroring the server rules; the server still validates content. */
export function checkFile(file: File, config: UploadConfig | null): string | null {
  const dot = file.name.lastIndexOf('.')
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (config && !config.supported_types.some((type) => type.extension === extension)) {
    const labels = [...new Set(config.supported_types.map((type) => type.label))].join(', ')
    return `Unsupported file type. Upload one of: ${labels}.`
  }
  if (file.size === 0) return 'The file is empty.'
  if (config && file.size > config.max_file_size) {
    return `File is larger than the ${formatBytes(config.max_file_size)} limit.`
  }
  return null
}

let nextId = 0

/**
 * Upload queue for one knowledge base. Files are sent one at a time so a large
 * batch doesn't saturate the connection. Successful uploads are reported via
 * `onUploaded` and leave the list; failures stay visible until dismissed.
 */
export function useDocumentUploads(
  knowledgeBaseId: string,
  config: UploadConfig | null,
  onUploaded: (document: DocumentItem) => void,
) {
  const [items, setItems] = useState<UploadItem[]>([])
  const pending = useRef<UploadItem[]>([])
  const running = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const onUploadedRef = useRef(onUploaded)

  useEffect(() => {
    onUploadedRef.current = onUploaded
  }, [onUploaded])

  // Cancel the in-flight upload and drop the queue when leaving the page.
  useEffect(
    () => () => {
      pending.current = []
      controller.current?.abort()
    },
    [],
  )

  const patch = (id: string, changes: Partial<UploadItem>) =>
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...changes } : item)))

  const drainQueue = useCallback(async () => {
    if (running.current) return
    running.current = true
    try {
      let item: UploadItem | undefined
      while ((item = pending.current.shift())) {
        const { id, file } = item
        controller.current = new AbortController()
        patch(id, { state: 'uploading', progress: 0 })
        try {
          const document = await uploadDocument(knowledgeBaseId, file, {
            signal: controller.current.signal,
            onProgress: (fraction) => patch(id, { progress: fraction }),
          })
          onUploadedRef.current(document)
          setItems((prev) => prev.filter((entry) => entry.id !== id))
        } catch (err) {
          if (err instanceof ApiError && err.code === 'aborted') return
          patch(id, { state: 'error', error: err instanceof ApiError ? err.message : 'Upload failed.' })
        }
      }
    } finally {
      running.current = false
      controller.current = null
    }
  }, [knowledgeBaseId])

  const addFiles = useCallback(
    (files: Iterable<File>) => {
      const added: UploadItem[] = [...files].map((file) => {
        const error = checkFile(file, config)
        return { id: `upload-${nextId++}`, file, state: error ? 'error' : 'queued', progress: 0, error }
      })
      setItems((prev) => [...prev, ...added])
      pending.current.push(...added.filter((item) => item.state === 'queued'))
      void drainQueue()
    },
    [config, drainQueue],
  )

  const dismiss = useCallback((id: string) => setItems((prev) => prev.filter((item) => item.id !== id)), [])

  return { items, addFiles, dismiss }
}
