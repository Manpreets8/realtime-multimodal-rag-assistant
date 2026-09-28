/**
 * Thin fetch wrapper that attaches the bearer token and normalises every
 * failure into an `ApiError` built from the backend's
 * `{ error: { code, message, request_id, details } }` envelope.
 */

import { tokenStorage } from './tokenStorage'

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export interface FieldError {
  loc: (string | number)[]
  message: string
  type: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly details: unknown

  constructor(status: number, code: string, message: string, requestId: string | null, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }

  /** Map validation errors (`loc: ["body", "email"]`) to `{ email: "message" }`. */
  fieldErrors(): Record<string, string> {
    if (this.code !== 'validation_error' || !Array.isArray(this.details)) return {}
    const result: Record<string, string> = {}
    for (const item of this.details as FieldError[]) {
      const field = item.loc?.at(-1)
      if (typeof field === 'string' && !(field in result)) {
        result[field] = item.message.replace(/^Value error, /, '')
      }
    }
    return result
  }
}

interface ErrorEnvelope {
  error: { code: string; message: string; request_id?: string | null; details?: unknown }
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    typeof value === 'object' &&
    value !== null &&
    'error' in value &&
    typeof (value as ErrorEnvelope).error?.message === 'string'
  )
}

let onUnauthorized: (() => void) | null = null

/** Registered by AuthProvider: called when an authenticated request gets a 401. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

export const NETWORK_ERROR_MESSAGE = 'Unable to reach the server. Check your connection.'
export const TIMEOUT_MESSAGE = 'The server took too long to respond. Please try again.'

/** Most requests should answer within seconds; a stuck request must not spin forever. */
export const DEFAULT_TIMEOUT_MS = 30_000
/** Requests that wait on the LLM or speech models. Longer than the server's own LLM timeout
 * (120 s) so the server can still return its specific error first. */
export const LONG_TIMEOUT_MS = 180_000



/** Build an ApiError from a failed response (shared by fetch- and XHR-based calls). */
export function toApiError(status: number, body: unknown, requestIdHeader: string | null, hadToken: boolean): ApiError {
  if (status === 401 && hadToken) onUnauthorized?.()
  if (isErrorEnvelope(body)) {
    const { code, message, request_id, details } = body.error
    return new ApiError(status, code, message, request_id ?? requestIdHeader, details)
  }
  return new ApiError(status, 'http_error', `Request failed with status ${status}.`, requestIdHeader)
}

/** Authorization header for the current session, if any. */
export function authHeader(): Record<string, string> {
  const token = tokenStorage.get()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** JSON-serialisable request body. */
  json?: unknown
  /** Non-2xx statuses whose JSON body should be returned instead of thrown (e.g. 503 readiness). */
  acceptStatuses?: number[]
  /** Give up after this long (default DEFAULT_TIMEOUT_MS). */
  timeoutMs?: number
}

async function send(path: string, init: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
  // One signal for both the caller's cancellation and our timeout.
  const controller = new AbortController()
  const caller = init.signal
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const forwardAbort = () => controller.abort(caller?.reason)
  if (caller?.aborted) forwardAbort()
  caller?.addEventListener('abort', forwardAbort, { once: true })
  try {
    return await fetch(`${API_BASE_URL}${path}`, { ...init, signal: controller.signal })
  } catch (error) {
    if (caller?.aborted) throw error // cancelled by the caller, not a network problem
    if (timedOut) throw new ApiError(0, 'timeout', TIMEOUT_MESSAGE, null)
    throw new ApiError(0, 'network_error', NETWORK_ERROR_MESSAGE, null)
  } finally {
    clearTimeout(timer)
    caller?.removeEventListener('abort', forwardAbort)
  }
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { acceptStatuses = [], headers: extraHeaders, json, timeoutMs, ...init } = options

  const headers = new Headers(extraHeaders)
  headers.set('Accept', 'application/json')
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  const token = tokenStorage.get()
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)

  const requestBody = json !== undefined ? JSON.stringify(json) : undefined
  const response = await send(path, { ...init, headers, body: requestBody }, timeoutMs)
  const body: unknown = response.status === 204 ? null : await response.json().catch(() => null)

  if (response.ok || acceptStatuses.includes(response.status)) {
    return body as T
  }
  throw toApiError(response.status, body, response.headers.get('X-Request-ID'), Boolean(token))
}

/**
 * Fetch a binary resource (a document download, synthesized speech) with the session's credentials.
 * With `json`, the request is a POST with that body.
 */
export async function apiBlob(
  path: string,
  options: { json?: unknown; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<{ blob: Blob; filename: string | null; headers: Headers }> {
  const hadToken = Boolean(tokenStorage.get())
  const headers = new Headers(authHeader())
  const init: RequestInit = { headers, signal: options.signal }
  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.method = 'POST'
    init.body = JSON.stringify(options.json)
  }
  const response = await send(path, init, options.timeoutMs)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw toApiError(response.status, body, response.headers.get('X-Request-ID'), hadToken)
  }
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('Content-Disposition')),
    headers: response.headers,
  }
}

/** Parse RFC 6266 Content-Disposition, preferring the UTF-8 `filename*` form. */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const extended = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header)
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim().replace(/^"|"$/g, ''))
    } catch {
      // fall through to the plain form
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1] : null
}

export interface UploadOptions {
  /** Fraction in [0, 1] of the request body sent so far. */
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
  /** Give up after this long; 0 = no limit (default: 5 minutes, enough for 25 MB on a slow link). */
  timeoutMs?: number
}

/**
 * POST multipart form data with upload progress. Uses XMLHttpRequest because
 * fetch() cannot report upload progress; errors are normalised like `apiRequest`.
 */
export function uploadForm<T>(path: string, form: FormData, options: UploadOptions = {}): Promise<T> {
  const { onProgress, signal, timeoutMs = 300_000 } = options

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const headers = authHeader()
    const hadToken = 'Authorization' in headers

    xhr.open('POST', `${API_BASE_URL}${path}`)
    xhr.setRequestHeader('Accept', 'application/json')
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total)
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        // non-JSON body (e.g. proxy error page): handled as a generic HTTP error below
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T)
      else reject(toApiError(xhr.status, body, xhr.getResponseHeader('X-Request-ID'), hadToken))
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', NETWORK_ERROR_MESSAGE, null))
    xhr.onabort = () => reject(new ApiError(0, 'aborted', 'Upload cancelled.', null))
    xhr.timeout = timeoutMs
    xhr.ontimeout = () => reject(new ApiError(0, 'timeout', TIMEOUT_MESSAGE, null))

    if (signal?.aborted) {
      reject(new ApiError(0, 'aborted', 'Upload cancelled.', null))
      return
    }
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(form)
  })
}
