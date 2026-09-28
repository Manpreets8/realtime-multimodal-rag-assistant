import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, DEFAULT_TIMEOUT_MS, LONG_TIMEOUT_MS, apiRequest, setUnauthorizedHandler } from './api'
import { sendMessage } from './chat'
import { tokenStorage } from './tokenStorage'

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('apiRequest', () => {
  it('returns parsed JSON on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ status: 'ok' }))

    await expect(apiRequest('/health')).resolves.toEqual({ status: 'ok' })
    expect(fetch).toHaveBeenCalledWith('/api/v1/health', expect.any(Object))
  })

  it('converts the backend error envelope into an ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ error: { code: 'not_found', message: 'Knowledge base not found.', request_id: 'req-1' } }, 404),
    )

    const error = await apiRequest('/knowledge-bases/x').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, code: 'not_found', message: 'Knowledge base not found.', requestId: 'req-1' })
  })

  it('returns the body for explicitly accepted non-2xx statuses', async () => {
    const body = { status: 'not_ready', checks: { database: false } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(body, 503))

    await expect(apiRequest('/health/ready', { acceptStatuses: [503] })).resolves.toEqual(body)
  })

  it('reports a network failure with a user-friendly message', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(apiRequest('/health')).rejects.toMatchObject({ code: 'network_error', status: 0 })
  })

  it('falls back to a generic error when the body is not an envelope', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 }))

    await expect(apiRequest('/health')).rejects.toMatchObject({ code: 'http_error', status: 502 })
  })

  it('attaches the stored bearer token and serialises JSON bodies', async () => {
    tokenStorage.set('abc123')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({}))

    await apiRequest('/auth/logout', { method: 'POST', json: { a: 1 } })

    const init = fetchSpy.mock.calls[0][1]!
    const headers = new Headers(init.headers)
    expect(headers.get('Authorization')).toBe('Bearer abc123')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init.body).toBe('{"a":1}')
  })

  it('notifies the unauthorized handler only for authenticated requests', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      jsonResponse({ error: { code: 'unauthorized', message: 'nope' } }, 401),
    )

    await apiRequest('/auth/login').catch(() => undefined)
    expect(handler).not.toHaveBeenCalled()

    tokenStorage.set('expired')
    await apiRequest('/auth/me').catch(() => undefined)
    expect(handler).toHaveBeenCalledOnce()
    setUnauthorizedHandler(null)
  })

  it('maps validation details to field errors', () => {
    const error = new ApiError(422, 'validation_error', 'Invalid', null, [
      { loc: ['body', 'password'], message: 'Value error, Password must contain a number.', type: 'value_error' },
      { loc: ['body', 'email'], message: 'Invalid email.', type: 'value_error' },
    ])

    expect(error.fieldErrors()).toEqual({ password: 'Password must contain a number.', email: 'Invalid email.' })
  })
})

/** A fetch that never answers, but rejects the way browsers do when its signal aborts. */
function hangingFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
      }),
  )
}

describe('request timeouts', () => {
  afterEach(() => vi.useRealTimers())

  it('gives up on a request that never answers', async () => {
    vi.useFakeTimers()
    hangingFetch()

    const request = apiRequest('/knowledge-bases').catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS)

    const error = (await request) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('timeout')
    expect(error.message).toBe('The server took too long to respond. Please try again.')
  })

  it('waits longer for requests that call the LLM', async () => {
    vi.useFakeTimers()
    hangingFetch()
    let settled = false

    const request = sendMessage({ message: 'Hi' })
      .catch((error: unknown) => error)
      .finally(() => (settled = true))
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS + 1000)
    expect(settled).toBe(false) // an LLM answer may legitimately take longer than 30 s

    await vi.advanceTimersByTimeAsync(LONG_TIMEOUT_MS)
    expect(((await request) as ApiError).code).toBe('timeout')
  })

  it('still distinguishes a cancelled request from a timeout', async () => {
    hangingFetch()
    const controller = new AbortController()

    const request = apiRequest('/knowledge-bases', { signal: controller.signal }).catch((error: unknown) => error)
    controller.abort()

    const error = await request
    expect(error).not.toBeInstanceOf(ApiError)
    expect((error as DOMException).name).toBe('AbortError')
  })
})
