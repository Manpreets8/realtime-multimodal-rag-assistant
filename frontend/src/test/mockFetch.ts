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
  'GET /dashboard': () => json(dashboardResponse()),
  'GET /knowledge-bases': () => json([]),
  'GET /conversations': () => json([]),
  'GET /system/providers': () =>
    json({
      app: 'Mindora AI',
      tagline: 'Your knowledge. One intelligent AI.',
      providers: [
        { capability: 'llm', provider: 'anthropic', model: 'claude-opus-5', runs: 'api', configured: true },
        { capability: 'reranking', provider: 'none', model: '', runs: 'off', configured: true },
      ],
    }),
}

/** A dashboard with no activity, for tests that land on "/" without caring about its content. */
export function dashboardResponse(overrides: { stats?: object; activity?: unknown[] } = {}) {
  const days = Array.from({ length: 14 }, (_, index) => ({
    date: new Date(Date.UTC(2026, 8, 17 + index)).toISOString().slice(0, 10),
    count: 0,
  }))
  return {
    stats: {
      knowledge_bases: 0,
      documents: { total: 0, indexed: 0, processing: 0, failed: 0 },
      conversations: 0,
      ai_answers: { days: 30, answers: 0, input_tokens: 0, output_tokens: 0, by_day: days },
      ...overrides.stats,
    },
    activity: overrides.activity ?? [],
  }
}

export const TEST_USER = {
  id: '6f1c2f4e-0000-4000-8000-000000000001',
  email: 'ada@example.com',
  full_name: 'Ada Lovelace',
  role: 'user' as const,
  is_active: true,
  created_at: '2026-09-26T00:00:00Z',
}

export function tokenResponse(token = 'token-abc') {
  return { access_token: token, token_type: 'bearer', expires_in: 3600, user: TEST_USER }
}
