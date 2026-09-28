import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../services/api'
import { IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE, deleteImage, uploadImage } from '../services/images'

export interface Attachment {
  localId: string
  file: File
  previewUrl: string
  state: 'uploading' | 'ready' | 'error'
  imageId: string | null
  error: string | null
}

let nextId = 0

export function checkImage(file: File): string | null {
  if (!(IMAGE_TYPES as readonly string[]).includes(file.type)) return 'Only PNG, JPEG, GIF and WebP images are supported.'
  if (file.size === 0) return 'The image is empty.'
  if (file.size > MAX_IMAGE_BYTES) return `Images must be at most ${MAX_IMAGE_BYTES / (1024 * 1024)} MB.`
  return null
}

/**
 * Images attached to the message being written. Each is uploaded as soon as it is
 * added, so sending only needs the image IDs. Removing an uploaded-but-unsent image
 * deletes it on the server.
 */
export function useImageAttachments() {
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const current = useRef<Attachment[]>([])

  useEffect(() => {
    current.current = attachments
  }, [attachments])

  // Revoke preview URLs when the composer goes away.
  useEffect(() => () => current.current.forEach((a) => URL.revokeObjectURL(a.previewUrl)), [])

  const patch = (localId: string, changes: Partial<Attachment>) =>
    setAttachments((prev) => prev.map((a) => (a.localId === localId ? { ...a, ...changes } : a)))

  const add = useCallback((files: Iterable<File>) => {
    setNotice(null)
    const room = MAX_IMAGES_PER_MESSAGE - current.current.length
    const incoming = [...files]
    if (incoming.length > room) setNotice(`You can attach up to ${MAX_IMAGES_PER_MESSAGE} images per message.`)
    for (const file of incoming.slice(0, Math.max(room, 0))) {
      const error = checkImage(file)
      if (error) {
        setNotice(`${file.name || 'Image'}: ${error}`)
        continue
      }
      const attachment: Attachment = {
        localId: `att-${nextId++}`,
        file,
        previewUrl: URL.createObjectURL(file),
        state: 'uploading',
        imageId: null,
        error: null,
      }
      current.current = [...current.current, attachment]
      setAttachments((prev) => [...prev, attachment])
      uploadImage(file)
        .then((image) => patch(attachment.localId, { state: 'ready', imageId: image.id }))
        .catch((err: unknown) =>
          patch(attachment.localId, { state: 'error', error: err instanceof ApiError ? err.message : 'Upload failed.' }),
        )
    }
  }, [])

  const remove = useCallback((localId: string) => {
    const attachment = current.current.find((a) => a.localId === localId)
    if (!attachment) return
    URL.revokeObjectURL(attachment.previewUrl)
    if (attachment.imageId) void deleteImage(attachment.imageId).catch(() => undefined) // best effort
    setAttachments((prev) => prev.filter((a) => a.localId !== localId))
  }, [])

  /** Forget the attachments after they were sent (the server now owns them). */
  const clear = useCallback(() => {
    setAttachments([])
    setNotice(null)
  }, [])

  const restore = useCallback((items: Attachment[]) => setAttachments(items), [])

  return {
    attachments,
    notice,
    add,
    remove,
    clear,
    restore,
    uploading: attachments.some((a) => a.state === 'uploading'),
    readyIds: attachments.filter((a) => a.state === 'ready').map((a) => a.imageId!),
  }
}
