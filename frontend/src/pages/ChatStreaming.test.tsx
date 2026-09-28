import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatMessage, ChatResponse, ConversationSummary } from '../services/chat'
import { CONNECTION_LOST_MESSAGE } from '../services/chatSocket'
import { TEST_USER, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

interface Frame {
  type: string
  id?: string | null
  [key: string]: unknown
}

/** In-memory stand-in for the browser WebSocket, driven by the test as the "server". */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static onConnect: ((socket: FakeWebSocket) => void) | null = null
  readonly url: string
  sent: Frame[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
    queueMicrotask(() => {
      this.onopen?.()
      FakeWebSocket.onConnect?.(this)
    })
  }

  send(data: string) {
    const frame = JSON.parse(data) as Frame
    this.sent.push(frame)
    if (frame.type === 'auth') this.emit({ type: 'ready' })
  }

  close(code = 1000) {
    this.onclose?.({ code })
  }

  emit(event: Frame) {
    act(() => this.onmessage?.({ data: JSON.stringify(event) }))
  }

  chatFrames() {
    return this.sent.filter((frame) => frame.type === 'chat')
  }
}

const socket = () => FakeWebSocket.instances.at(-1)!

async function nextChat(): Promise<Frame> {
  await waitFor(() => expect(FakeWebSocket.instances.at(-1)?.chatFrames().length).toBeGreaterThan(0))
  return socket().chatFrames().at(-1)!
}

const SUMMARY: ConversationSummary = {
  id: 'conv-9', title: 'How much annual leave?', knowledge_base_id: null, knowledge_base_name: null,
  message_count: 2, last_message_preview: 'Employees get 18 days.', created_at: '2026-09-27T10:00:00Z',
  updated_at: '2026-09-27T10:00:00Z',
}

function message(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return {
    id, role, content, created_at: '2026-09-27T10:00:00Z', answer_type: role === 'assistant' ? 'general' : null,
    grounded: false, knowledge_base_id: null, retrieval_query: null, model: null, usage: null, truncated: false,
    timings_ms: null, retrieval: null, citations: [], sources: [],
  }
}

const RESPONSE: ChatResponse = {
  conversation: SUMMARY,
  user_message: message('m1', 'user', 'How much annual leave?'),
  assistant_message: message('m2', 'assistant', 'Employees get 18 days of paid annual leave.'),
}

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /conversations': () => json([]),
    'GET /knowledge-bases': () => json([]),
    'GET /conversations/conv-9': () => json({ ...SUMMARY, messages: [RESPONSE.user_message, RESPONSE.assistant_message] }),
    ...extra,
  })
}

async function ask(text = 'How much annual leave?') {
  renderSignedIn('/chat')
  await userEvent.type(await screen.findByLabelText('Message', { exact: true }), `${text}{Enter}`)
}

describe('Streaming chat over WebSocket', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    FakeWebSocket.onConnect = null
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('authenticates, shows each stage and the answer as it is written, then the saved answer', async () => {
    const fetchSpy = backend()
    await ask()

    const chat = await nextChat()
    const ws = socket()
    expect(ws.url).toMatch(/^ws:\/\/[^/]+\/api\/v1\/ws\/chat$/)
    expect(ws.sent[0]).toEqual({ type: 'auth', token: 'test-token' }) // token in a frame, never the URL
    expect(chat).toEqual({ type: 'chat', id: expect.any(String), message: 'How much annual leave?', knowledge_base_id: null })

    const id = chat.id
    ws.emit({ type: 'status', id, stage: 'retrieving' })
    expect(screen.getByRole('status')).toHaveTextContent('Searching your documents…')
    ws.emit({ type: 'status', id, stage: 'reranking' })
    expect(screen.getByRole('status')).toHaveTextContent('Ranking the most relevant passages…')
    ws.emit({ type: 'sources', id, sources: [{ number: 1, filename: 'a.pdf', page_number: 1, section: null }, { number: 2, filename: 'b.pdf', page_number: 3, section: null }] })
    ws.emit({ type: 'status', id, stage: 'generating' })
    expect(screen.getByRole('status')).toHaveTextContent('Writing an answer from 2 passages…')

    ws.emit({ type: 'delta', id, text: 'Employees get ' })
    ws.emit({ type: 'delta', id, text: '18 days' })
    const streaming = screen.getByTestId('streaming-answer')
    expect(streaming).toHaveTextContent('Employees get 18 days')
    expect(streaming).toHaveAttribute('aria-live', 'off') // not read out word by word
    expect(screen.getByRole('button', { name: 'Stop generating' })).toBeInTheDocument()

    ws.emit({ type: 'done', id, response: RESPONSE })

    const answer = await screen.findByRole('article', { name: 'Assistant message' })
    expect(answer).toHaveTextContent('Employees get 18 days of paid annual leave.')
    expect(screen.queryByTestId('streaming-answer')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeInTheDocument()
    expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/chat'))).toBe(false) // no REST call
  })

  it('reuses one connection for later messages', async () => {
    backend()
    await ask()
    const first = await nextChat()
    socket().emit({ type: 'done', id: first.id, response: RESPONSE })
    await screen.findByRole('article', { name: 'Assistant message' })

    await userEvent.type(screen.getByLabelText('Message', { exact: true }), 'And sick leave?{Enter}')

    await waitFor(() => expect(socket().chatFrames()).toHaveLength(2))
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(socket().chatFrames()[1]).toMatchObject({ message: 'And sick leave?', conversation_id: 'conv-9' })
  })

  it('discards streamed text when a fallback model restarts the answer', async () => {
    backend()
    await ask()
    const { id } = await nextChat()

    socket().emit({ type: 'delta', id, text: 'Partial text from a model that declined' })
    socket().emit({ type: 'restart', id })
    socket().emit({ type: 'delta', id, text: 'Fresh answer' })

    expect(screen.getByTestId('streaming-answer')).toHaveTextContent(/^Fresh answer$/)
  })

  it('Stop cancels the answer, saves nothing and puts the message back', async () => {
    backend()
    await ask('Write me a long essay')
    const { id } = await nextChat()
    socket().emit({ type: 'delta', id, text: 'Once upon' })

    await userEvent.click(screen.getByRole('button', { name: 'Stop generating' }))

    expect(socket().sent.at(-1)).toEqual({ type: 'cancel', id })
    socket().emit({ type: 'cancelled', id })
    expect(await screen.findByText(/Stopped. Nothing was saved/)).toBeInTheDocument()
    expect(screen.getByLabelText('Message', { exact: true })).toHaveValue('Write me a long essay')
    expect(screen.queryByTestId('streaming-answer')).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Your message' })).not.toBeInTheDocument()
  })

  it('shows server errors with Retry', async () => {
    backend()
    await ask()
    const { id } = await nextChat()

    socket().emit({ type: 'error', id, error: { code: 'llm_timeout', message: 'The AI model took too long to respond.', request_id: 'r1' } })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Not sent: The AI model took too long to respond.')
    expect(alert).toHaveTextContent('Ref r1') // request reference for support
    const sent = screen.getByRole('article', { name: 'Your message' })

    await userEvent.click(within(sent).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(socket().chatFrames()).toHaveLength(2))
  })

  it('reports a dropped connection mid-answer and reconnects for the retry', async () => {
    backend()
    await ask()
    await nextChat()

    act(() => socket().close(1006))

    expect(await screen.findByText(`Not sent: ${CONNECTION_LOST_MESSAGE}`)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2))
    await waitFor(() => expect(socket().chatFrames()).toHaveLength(1))
  })

  it('falls back to POST /chat when the WebSocket cannot connect', async () => {
    FakeWebSocket.onConnect = (ws) => ws.close(1006) // e.g. a proxy that blocks WebSockets
    FakeWebSocket.prototype.send = function (this: FakeWebSocket, data: string) {
      this.sent.push(JSON.parse(data) as Frame) // never answers `ready`
    }
    const fetchSpy = backend({ 'POST /chat': () => json(RESPONSE) })
    try {
      await ask()

      expect(await screen.findByText('Employees get 18 days of paid annual leave.')).toBeInTheDocument()
      expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/chat'))).toBe(true)
    } finally {
      FakeWebSocket.prototype.send = function (this: FakeWebSocket, data: string) {
        const frame = JSON.parse(data) as Frame
        this.sent.push(frame)
        if (frame.type === 'auth') this.emit({ type: 'ready' })
      }
    }
  })

  it('signs out when the server rejects the session', async () => {
    FakeWebSocket.prototype.send = function (this: FakeWebSocket, data: string) {
      const frame = JSON.parse(data) as Frame
      this.sent.push(frame)
      if (frame.type === 'auth') {
        this.emit({ type: 'error', id: null, error: { code: 'unauthorized', message: 'Your session has ended. Please log in again.' } })
        this.close(4401)
      }
    }
    backend()
    try {
      await ask()
      expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
    } finally {
      FakeWebSocket.prototype.send = function (this: FakeWebSocket, data: string) {
        const frame = JSON.parse(data) as Frame
        this.sent.push(frame)
        if (frame.type === 'auth') this.emit({ type: 'ready' })
      }
    }
  })
})
