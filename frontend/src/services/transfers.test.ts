import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, uploadForm } from './api'
import { downloadDocument, openPdfInNewTab } from './documents'
import { uploadImage } from './images'
import { tokenStorage } from './tokenStorage'
import { synthesizeSpeech, transcribeAudio } from './voice'

/** Stand-in for XMLHttpRequest (jsdom's would try a real network request). */
class FakeXhr {
  static last: FakeXhr
  method = ''
  url = ''
  headers: Record<string, string> = {}
  body: unknown = null
  status = 0
  responseText = ''
  timeout = 0
  responseHeaders: Record<string, string> = {}
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  ontimeout: (() => void) | null = null

  constructor() {
    FakeXhr.last = this
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
  send(body: unknown) {
    this.body = body
  }
  abort() {
    this.onabort?.()
  }
  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.responseHeaders = headers
    this.onload?.()
  }
}

describe('uploads (XMLHttpRequest)', () => {
  beforeEach(() => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    tokenStorage.set('token-1')
  })
  afterEach(() => vi.unstubAllGlobals())

  it('posts the form with the token, reports progress and resolves with the JSON body', async () => {
    const progress: number[] = []
    const file = new File(['png'], 'shot.png', { type: 'image/png' })

    const upload = uploadImage(file, { onProgress: (f) => progress.push(f) })
    const xhr = FakeXhr.last
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 })
    xhr.respond(201, { id: 'img-1' })

    expect(await upload).toEqual({ id: 'img-1' })
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/v1/images')
    expect(xhr.headers.Authorization).toBe('Bearer token-1')
    expect((xhr.body as FormData).get('file')).toBe(file)
    expect(progress).toEqual([0.5])
    expect(xhr.timeout).toBe(300_000)
  })

  it('turns the error envelope into an ApiError with the request id', async () => {
    const upload = uploadForm('/documents/upload', new FormData())
    FakeXhr.last.respond(
      415,
      { error: { code: 'unsupported_file_type', message: 'This file type is not supported.', request_id: 'req-9' } },
      { 'X-Request-ID': 'req-9' },
    )

    await expect(upload).rejects.toMatchObject({ status: 415, code: 'unsupported_file_type', requestId: 'req-9' })
  })

  it.each([
    ['timeout', (xhr: FakeXhr) => xhr.ontimeout?.(), 'The server took too long to respond. Please try again.'],
    ['network_error', (xhr: FakeXhr) => xhr.onerror?.(), 'Unable to reach the server. Check your connection.'],
  ])('reports %s with a user-facing message', async (code, fail, message) => {
    const upload = uploadForm('/images', new FormData())
    fail(FakeXhr.last)

    const error = (await upload.catch((e: unknown) => e)) as ApiError
    expect(error.code).toBe(code)
    expect(error.message).toBe(message)
  })

  it('can be cancelled', async () => {
    const controller = new AbortController()
    const upload = uploadForm('/images', new FormData(), { signal: controller.signal })
    controller.abort()

    await expect(upload).rejects.toMatchObject({ code: 'aborted' })
  })

  it('transcribes a recording with a long timeout and a file name matching its format', async () => {
    const recording = new Blob(['ogg'], { type: 'audio/ogg;codecs=opus' })

    const result = transcribeAudio(recording)
    const xhr = FakeXhr.last
    xhr.respond(200, { text: 'hello' })

    expect(await result).toEqual({ text: 'hello' })
    expect(xhr.url).toBe('/api/v1/voice/transcribe')
    expect(((xhr.body as FormData).get('file') as File).name).toBe('recording.ogg')
    expect(xhr.timeout).toBe(180_000)
  })
})

describe('binary responses', () => {
  beforeEach(() => tokenStorage.set('token-1'))
  afterEach(() => vi.restoreAllMocks())

  it('synthesizes speech with a JSON POST and reports truncation', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('mp3', { headers: { 'X-Speech-Truncated': 'true', 'Content-Type': 'audio/mpeg' } }))

    const speech = await synthesizeSpeech('Hello.')

    const [url, init] = fetchSpy.mock.calls[0]
    expect(url).toBe('/api/v1/voice/synthesize')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify({ text: 'Hello.' }))
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer token-1')
    expect(speech.truncated).toBe(true)
    expect(await speech.audio.text()).toBe('mp3')
  })

  it('downloads a document under the server-provided file name', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('%PDF', { headers: { 'Content-Disposition': "attachment; filename*=utf-8''L%C3%A9ave%20policy.pdf" } }),
    )
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await downloadDocument({ id: 'doc-1', filename: 'fallback.pdf' })

    const link = click.mock.contexts[0] as HTMLAnchorElement
    expect(link.download).toBe('Léave policy.pdf')
    expect(link.href).toMatch(/^blob:/)
  })

  it('opens a PDF at the cited page in a tab opened during the click', async () => {
    const tab = { opener: {} as unknown, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('%PDF'))

    await openPdfInNewTab('doc-1', 3)

    expect(fetchSpy.mock.calls[0][0]).toBe('/api/v1/documents/doc-1/download?inline=true')
    expect(tab.opener).toBeNull() // the opened page can't reach back into the app
    expect(tab.location.href).toMatch(/^blob:.*#page=3$/)
  })

  it('closes the tab again if the PDF cannot be fetched', async () => {
    const tab = { opener: null, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'not_found', message: 'Document not found.' } }), { status: 404 }),
    )

    await expect(openPdfInNewTab('doc-1')).rejects.toMatchObject({ code: 'not_found' })
    expect(tab.close).toHaveBeenCalled()
  })
})
