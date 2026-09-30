import { vi } from 'vitest'

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>

export function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function errorEnvelope(status: number, code: string, message: string, details?: unknown): Response {
  return json({ error: { code, message, request_id: 'req-test', details } }, status)
}

/**
 * Route fetch calls by "METHOD /path" (path relative to /api/v1).
 * Unmatched requests fail the test loudly instead of hanging.
 */
export function mockFetch(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    const key = `${(init?.method ?? 'GET').toUpperCase()} ${url.pathname.replace(/^\/api\/v1/, '')}`
    const handler = routes[key]
    if (!handler) throw new Error(`Unmocked request: ${key}`)
    return handler(init)
  })
}

export const HEALTHY_BACKEND: Record<string, Handler> = {
  'GET /health': () => json({ status: 'ok', app: 'Mindora AI', version: '0.1.0', environment: 'test' }),
  'GET /health/ready': () =>
    json({
      status: 'ready',
      checks: { database: true, pgvector: true },
      services: { redis: true, ingestion_workers: 1, ingestion_waiting: 0 },
    }),
}

export const TEST_USER = {
  id: '6f1c2f4e-0000-4000-8000-000000000001',
  email: 'ada@example.com',
  full_name: 'Ada Lovelace',
  is_active: true,
  created_at: '2026-09-26T00:00:00Z',
}

export function tokenResponse(token = 'token-abc') {
  return { access_token: token, token_type: 'bearer', expires_in: 3600, user: TEST_USER }
}
