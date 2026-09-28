import { apiBlob, apiRequest, uploadForm, type UploadOptions } from './api'

export interface UploadedImage {
  id: string
  filename: string
  media_type: string
  width: number
  height: number
  size_bytes: number
}

/** Mirrors the server defaults (MAX_IMAGE_SIZE, MAX_IMAGES_PER_MESSAGE); the server still validates. */
export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_IMAGES_PER_MESSAGE = 4

export function uploadImage(file: File, options: UploadOptions = {}): Promise<UploadedImage> {
  const form = new FormData()
  form.append('file', file)
  return uploadForm<UploadedImage>('/images', form, options)
}

export function deleteImage(id: string): Promise<null> {
  return apiRequest<null>(`/images/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Fetch a stored image with the session token (an <img src> cannot send the Authorization header). */
export async function fetchImageBlob(id: string): Promise<Blob> {
  return (await apiBlob(`/images/${encodeURIComponent(id)}/content`)).blob
}
