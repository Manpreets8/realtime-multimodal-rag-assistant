import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { filenameFromDisposition } from './api'
import { uploadDocument } from './documents'
import { tokenStorage } from './tokenStorage'

/** Minimal controllable XMLHttpRequest stand-in. */
class FakeXHR {
  static last: FakeXHR
  method = ''
  url = ''
  headers: Record<string, string> = {}
  body: FormData | null = null
  status = 0
  responseText = ''
  responseHeaders: Record<string, string> = {}
  upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  aborted = false

  constructor() {
    FakeXHR.last = this
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  getResponseHeader(name: string) {
    return this.responseHeaders[name] ?? null
  }
  send(body: FormData) {
    this.body = body
  }
  abort() {
    this.aborted = true
    this.onabort?.()
  }
  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status
    this.responseText = typeof body === 'string' ? body : JSON.stringify(body)
    this.responseHeaders = headers
    this.onload?.()
  }
}

describe('uploadDocument', () => {
  beforeEach(() => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    tokenStorage.set('tok')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts multipart form data with auth and reports progress', async () => {
    const onProgress = vi.fn()
    const file = new File(['hello'], 'notes.md')
    const promise = uploadDocument('kb-1', file, { onProgress })
    const xhr = FakeXHR.last

    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 } as ProgressEvent)
    xhr.respond(201, { id: 'doc-1', filename: 'notes.md' })

    await expect(promise).resolves.toMatchObject({ id: 'doc-1' })
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/v1/documents/upload')
    expect(xhr.headers.Authorization).toBe('Bearer tok')
    expect(xhr.body).not.toBeNull()
    expect(xhr.body!.get('knowledge_base_id')).toBe('kb-1')
    expect((xhr.body!.get('file') as File).name).toBe('notes.md')
    expect(onProgress).toHaveBeenCalledWith(0.5)
  })

  it('rejects with the server error envelope', async () => {
    const promise = uploadDocument('kb-1', new File(['x'], 'a.exe'))

    FakeXHR.last.respond(415, { error: { code: 'unsupported_file_type', message: 'Not supported.', request_id: 'r1' } })

    await expect(promise).rejects.toMatchObject({ status: 415, code: 'unsupported_file_type', message: 'Not supported.' })
  })

  it('handles non-JSON error bodies and network failures', async () => {
    const badGateway = uploadDocument('kb-1', new File(['x'], 'a.pdf'))
    FakeXHR.last.respond(502, '<html>Bad gateway</html>')
    await expect(badGateway).rejects.toMatchObject({ status: 502, code: 'http_error' })

    const offline = uploadDocument('kb-1', new File(['x'], 'a.pdf'))
    FakeXHR.last.onerror?.()
    await expect(offline).rejects.toMatchObject({ code: 'network_error' })
  })

  it('can be cancelled with an AbortSignal', async () => {
    const controller = new AbortController()
    const promise = uploadDocument('kb-1', new File(['x'], 'a.pdf'), { signal: controller.signal })

    controller.abort()

    await expect(promise).rejects.toMatchObject({ code: 'aborted' })
    expect(FakeXHR.last.aborted).toBe(true)
  })
})

describe('filenameFromDisposition', () => {
  it.each([
    [`attachment; filename="report.pdf"`, 'report.pdf'],
    [`attachment; filename*=utf-8''caf%C3%A9%20notes.md`, 'café notes.md'],
    [`attachment; filename="fallback.txt"; filename*=UTF-8''%E2%82%AC.txt`, '€.txt'],
    [null, null],
  ])('%s -> %s', (header, expected) => {
    expect(filenameFromDisposition(header)).toBe(expected)
  })
})
