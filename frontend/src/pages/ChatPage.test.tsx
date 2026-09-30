import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ChatMessage, ChatResponse, ConversationDetail, ConversationSummary } from '../services/chat'
import { TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const KB = {
  id: 'kb-1',
  name: 'Company Policies',
  description: null,
  document_count: 2,
  status_counts: { completed: 2 },
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
}

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: 'conv-1',
    title: 'How much annual leave?',
    knowledge_base_id: 'kb-1',
    knowledge_base_name: 'Company Policies',
    message_count: 2,
    last_message_preview: '18 days.',
    created_at: '2026-09-26T10:00:00Z',
    updated_at: '2026-09-26T10:00:00Z',
    ...overrides,
  }
}

function userMessage(id: string, content: string): ChatMessage {
  return {
    id, role: 'user', content, created_at: '2026-09-26T10:00:00Z', answer_type: null, grounded: false,
    knowledge_base_id: null, retrieval_query: null, model: null, usage: null, truncated: false,
    timings_ms: null, retrieval: null, citations: [], sources: [],
  }
}

function assistantMessage(id: string, content: string, overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    ...userMessage(id, content),
    role: 'assistant',
    answer_type: 'knowledge_base',
    grounded: true,
    knowledge_base_id: 'kb-1',
    model: 'claude-opus-5',
    usage: { input_tokens: 900, output_tokens: 20 },
    citations: [
      {
        source_number: 1, document_id: 'd1', filename: 'handbook.pdf', page_number: 2, section: null,
        quotes: [{ text: '18 days of paid annual leave', start: 14, end: 42 }], answer_spans: [[0, content.length]],
      },
    ],
    sources: [
      {
        number: 1, chunk_id: 'c1', document_id: 'd1', knowledge_base_id: 'kb-1', filename: 'handbook.pdf',
        page_number: 2, section: null, content: 'Employees get 18 days of paid annual leave.', rerank_score: 3, similarity: 0.7,
      },
    ],
    ...overrides,
  }
}

const EXISTING: ConversationDetail = {
  ...summary(),
  messages: [userMessage('m1', 'How much annual leave?'), assistantMessage('m2', 'Employees get 18 days.')],
}

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /conversations': () => json([summary()]),
    'GET /knowledge-bases': () => json([KB]),
    'GET /conversations/conv-1': () => json(EXISTING),
    ...extra,
  })
}

const composer = () => screen.getByLabelText('Message')

function bodyOf(fetchSpy: ReturnType<typeof mockFetch>, path: string, method = 'POST') {
  const call = fetchSpy.mock.calls.filter(([url, init]) => String(url).endsWith(path) && init?.method === method).at(-1)
  return call ? JSON.parse(String(call[1]?.body)) : undefined
}

describe('ChatPage', () => {
  it('starts a new conversation with the knowledge base from the URL', async () => {
    const response: ChatResponse = {
      conversation: summary({ id: 'conv-new', title: 'What is the leave policy?' }),
      user_message: userMessage('u1', 'What is the leave policy?'),
      assistant_message: assistantMessage('a1', 'Employees get 18 days of paid annual leave.'),
    }
    const fetchSpy = backend({
      'GET /conversations': () => json([]),
      'POST /chat': () => json(response),
      'GET /conversations/conv-new': () => json({ ...response.conversation, messages: [response.user_message, response.assistant_message] }),
    })
    renderSignedIn('/chat?kb=kb-1')

    expect(await screen.findByText('How can I help?')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('Knowledge base')).toHaveValue('kb-1'))
    await userEvent.type(composer(), 'What is the leave policy?{Enter}')

    const answer = await screen.findByRole('article', { name: 'Assistant message' })
    expect(within(answer).getByText('Answered from your documents')).toBeInTheDocument()
    expect(within(answer).getByRole('button', { name: 'Source 1: handbook.pdf, Page 2' })).toBeInTheDocument()
    expect(bodyOf(fetchSpy, '/chat')).toEqual({ message: 'What is the leave policy?', knowledge_base_id: 'kb-1' })
    expect(screen.getByRole('heading', { name: 'What is the leave policy?' })).toBeInTheDocument()
    expect(within(screen.getByRole('navigation', { name: 'Conversations' })).getByText('What is the leave policy?')).toBeInTheDocument()
    expect(composer()).toHaveValue('')
  })

  it('loads history and sends follow-ups to the same conversation', async () => {
    const fetchSpy = backend({
      'POST /chat': () =>
        json({
          conversation: summary({ message_count: 4 }),
          user_message: userMessage('m3', 'Can it carry over?'),
          assistant_message: assistantMessage('m4', 'Up to 5 days carry over.', { retrieval_query: 'annual leave carry over' }),
        }),
    })
    renderSignedIn('/chat/conv-1')

    expect(await screen.findByText('Employees get 18 days.')).toBeInTheDocument()
    await userEvent.type(composer(), 'Can it carry over?')
    await userEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText('Up to 5 days carry over.')).toBeInTheDocument()
    expect(screen.getByText('Searched for: “annual leave carry over”')).toBeInTheDocument()
    // Follow-ups name the conversation and don't re-send the knowledge base.
    expect(bodyOf(fetchSpy, '/chat')).toEqual({ message: 'Can it carry over?', conversation_id: 'conv-1' })
    expect(screen.getAllByText(/Employees get 18 days\.|Up to 5 days carry over\./)).toHaveLength(2)
  })

  it('keeps a failed message with Retry and Edit, and saves nothing until it succeeds', async () => {
    let attempts = 0
    backend({
      'POST /chat': () =>
        ++attempts === 1
          ? errorEnvelope(504, 'llm_timeout', 'The AI model took too long to respond. Please try again.')
          : json({
              conversation: summary({ message_count: 4 }),
              user_message: userMessage('m3', 'And sick leave?'),
              assistant_message: assistantMessage('m4', '10 days of sick leave.'),
            }),
    })
    renderSignedIn('/chat/conv-1')
    await screen.findByText('Employees get 18 days.')

    await userEvent.type(composer(), 'And sick leave?{Enter}')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Not sent: The AI model took too long to respond.')
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('10 days of sick leave.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(attempts).toBe(2)
  })

  it('Edit puts a failed message back into the composer', async () => {
    backend({ 'POST /chat': () => errorEnvelope(503, 'llm_unavailable', 'The AI model is busy right now.') })
    renderSignedIn('/chat/conv-1')
    await screen.findByText('Employees get 18 days.')

    await userEvent.type(composer(), 'Draft question{Enter}')
    await userEvent.click(within(await screen.findByRole('alert')).getByRole('button', { name: 'Edit' }))

    expect(composer()).toHaveValue('Draft question')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('Shift+Enter adds a new line instead of sending', async () => {
    const fetchSpy = backend()
    renderSignedIn('/chat/conv-1')
    await screen.findByText('Employees get 18 days.')

    await userEvent.type(composer(), 'line one{Shift>}{Enter}{/Shift}line two')

    expect(composer()).toHaveValue('line one\nline two')
    expect(bodyOf(fetchSpy, '/chat')).toBeUndefined()
  })

  it('switching knowledge base on an existing conversation saves it immediately', async () => {
    const fetchSpy = backend({
      'PATCH /conversations/conv-1': () => json(summary({ knowledge_base_id: null, knowledge_base_name: null })),
    })
    renderSignedIn('/chat/conv-1')
    await screen.findByText('Employees get 18 days.')

    await userEvent.selectOptions(screen.getByLabelText('Knowledge base'), '')

    expect(bodyOf(fetchSpy, '/conversations/conv-1', 'PATCH')).toEqual({ knowledge_base_id: null })
    await waitFor(() => expect(screen.getByLabelText('Knowledge base')).toHaveValue(''))
  })

  it('deletes a conversation after confirmation and returns to a new chat', async () => {
    backend({ 'DELETE /conversations/conv-1': () => json(null, 204) })
    renderSignedIn('/chat/conv-1')
    await screen.findByText('Employees get 18 days.')

    await userEvent.click(screen.getByRole('button', { name: 'Delete conversation How much annual leave?' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete conversation' }))

    expect(await screen.findByText('How can I help?')).toBeInTheDocument()
    expect(screen.getByText('No conversations yet.')).toBeInTheDocument()
  })

  it('opens citations from old messages even after the document was deleted', async () => {
    const deleted = assistantMessage('m2', 'Employees get 18 days.')
    deleted.sources[0] = { ...deleted.sources[0], chunk_id: null, document_id: null }
    backend({ 'GET /conversations/conv-1': () => json({ ...EXISTING, messages: [EXISTING.messages[0], deleted] }) })
    renderSignedIn('/chat/conv-1')

    await userEvent.click(await screen.findByRole('button', { name: 'Source 1: handbook.pdf, Page 2' }))

    const viewer = screen.getByRole('dialog')
    expect(within(viewer).getByRole('note')).toHaveTextContent('no longer available')
    expect(within(viewer).getByText('18 days of paid annual leave', { selector: 'mark' })).toBeInTheDocument()
  })

  it('shows a clear message for a conversation that does not exist', async () => {
    backend({ 'GET /conversations/conv-1': () => errorEnvelope(404, 'not_found', 'Conversation not found.') })
    renderSignedIn('/chat/conv-1')

    expect(await screen.findByText('This conversation does not exist or was deleted.')).toBeInTheDocument()
    expect(composer()).toBeDisabled()
  })

  it('starts new chats with the knowledge base you used last', async () => {
    backend({ 'GET /conversations': () => json([]) })
    const first = renderSignedIn('/chat')

    await screen.findByRole('option', { name: 'Company Policies' })
    await userEvent.selectOptions(screen.getByLabelText('Knowledge base'), 'kb-1')
    expect(localStorage.getItem('mindora-last-kb')).toBe('kb-1')
    first.unmount()

    renderSignedIn('/chat')
    await waitFor(() => expect(screen.getByLabelText('Knowledge base')).toHaveValue('kb-1'))
  })

  it('ignores a remembered knowledge base that was deleted', async () => {
    localStorage.setItem('mindora-last-kb', 'kb-deleted')
    const fetchSpy = backend({
      'GET /conversations': () => json([]),
      'POST /chat': () => json({ conversation: summary({ id: 'conv-g', knowledge_base_id: null }), user_message: userMessage('u', 'Hi'), assistant_message: assistantMessage('a', 'Hello!', { answer_type: 'general', grounded: false, citations: [], sources: [] }) }),
      'GET /conversations/conv-g': () => json({ ...summary({ id: 'conv-g', knowledge_base_id: null }), messages: [] }),
    })
    renderSignedIn('/chat')

    await screen.findByRole('option', { name: 'Company Policies' })
    expect(screen.getByLabelText('Knowledge base')).toHaveValue('')
    await userEvent.type(composer(), 'Hi{Enter}')
    await waitFor(() => expect(bodyOf(fetchSpy, '/chat')).toEqual({ message: 'Hi', knowledge_base_id: null }))
  })
})
