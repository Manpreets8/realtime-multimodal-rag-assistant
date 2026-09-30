import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { HEALTHY_BACKEND, TEST_USER, dashboardResponse, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const KB = {
  id: 'kb-1', name: 'Company Policies', description: null, document_count: 7,
  status_counts: { completed: 5, processing: 2 }, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-25T10:00:00Z',
}
const CONVERSATION = {
  id: 'c-1', title: 'How much annual leave?', knowledge_base_id: 'kb-1', knowledge_base_name: 'Company Policies',
  message_count: 4, last_message_preview: '20 days.', created_at: '2026-09-26T10:00:00Z', updated_at: new Date().toISOString(),
}

function busyWorkspace() {
  const response = dashboardResponse({
    stats: {
      knowledge_bases: 2,
      documents: { total: 10, indexed: 8, processing: 1, failed: 1 },
      conversations: 3,
    },
    activity: [
      { kind: 'document_failed', at: new Date().toISOString(), title: 'scan.pdf', detail: 'No text found.', knowledge_base_id: 'kb-1', conversation_id: null },
      { kind: 'conversation_started', at: '2026-09-29T08:00:00Z', title: 'How much annual leave?', detail: null, knowledge_base_id: null, conversation_id: 'c-1' },
    ],
  })
  response.stats.ai_answers = {
    ...response.stats.ai_answers,
    answers: 12,
    input_tokens: 9_000,
    output_tokens: 1_500,
    by_day: response.stats.ai_answers.by_day.map((day, index) => ({ ...day, count: index === 13 ? 3 : 0 })),
  }
  return response
}

function backend(dashboard: unknown, extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    ...HEALTHY_BACKEND,
    'GET /auth/me': () => json(TEST_USER),
    'GET /dashboard': () => json(dashboard),
    'GET /knowledge-bases': () => json([KB]),
    'GET /conversations': () => json([CONVERSATION]),
    ...extra,
  })
}

describe('Dashboard', () => {
  it('guides a new user through getting started', async () => {
    backend(dashboardResponse(), { 'GET /knowledge-bases': () => json([]), 'GET /conversations': () => json([]) })
    renderSignedIn('/')

    expect(await screen.findByRole('heading', { name: 'Getting started' })).toBeInTheDocument()
    expect(screen.getByText('Set up your first knowledge base to start asking questions.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Workspace summary' })).not.toBeInTheDocument()
  })

  it('summarises the workspace with real totals', async () => {
    backend(busyWorkspace())
    renderSignedIn('/')

    const summary = await screen.findByRole('region', { name: 'Workspace summary' })
    expect(within(summary).getByRole('link', { name: /Knowledge bases\s*2/ })).toHaveAttribute('href', '/knowledge-bases')
    expect(within(summary).getByRole('link', { name: /Documents\s*10\s*8 indexed · 1 processing · 1 failed/ })).toHaveAttribute(
      'href',
      '/documents',
    )
    expect(within(summary).getByRole('link', { name: /Conversations\s*3/ })).toBeInTheDocument()
    expect(within(summary).getByText('AI answers · 30 days')).toBeInTheDocument()
    expect(within(summary).getByText('12')).toBeInTheDocument()
    expect(within(summary).getByText('10.5K tokens in chat')).toBeInTheDocument()
  })

  it('charts answers per day, with a table for screen readers and a hover tooltip', async () => {
    const { container } = (backend(busyWorkspace()), renderSignedIn('/'))

    const table = await screen.findByRole('table', { name: 'Chat answers per day, last 14 days' })
    expect(within(table).getAllByRole('row')).toHaveLength(15) // header + 14 days
    const columns = container.querySelectorAll('svg g')
    fireEvent.mouseEnter(columns[columns.length - 1])
    expect(screen.getByText(/· 3 answers$/)).toBeInTheDocument()
  })

  it('lists recent activity with links to what it describes', async () => {
    backend(busyWorkspace())
    renderSignedIn('/')

    const activity = await screen.findByRole('region', { name: 'Recent activity' })
    const failed = within(activity).getByRole('link', { name: /Processing failed scan\.pdf/ })
    expect(failed).toHaveAttribute('href', '/knowledge-bases/kb-1')
    expect(within(failed).getByText('No text found.')).toBeInTheDocument()
    expect(within(activity).getByRole('link', { name: /Started a conversation How much annual leave\?/ })).toHaveAttribute(
      'href',
      '/chat/c-1',
    )
    const recent = screen.getByRole('region', { name: 'Recent conversations' })
    expect(within(recent).getByRole('link', { name: /How much annual leave\?/ })).toHaveAttribute('href', '/chat/c-1')
    expect(within(screen.getByRole('region', { name: 'Knowledge bases' })).getByText('· 2 processing')).toBeInTheDocument()
  })

  it('shows which AI services are configured', async () => {
    backend(busyWorkspace())
    renderSignedIn('/')

    expect(await screen.findByTestId('ai-llm')).toHaveTextContent('API')
    expect(screen.getByText('anthropic · claude-opus-5')).toBeInTheDocument()
    expect(screen.getByTestId('ai-reranking')).toHaveTextContent('Off')
  })

  it('recovers from a failed load', async () => {
    let fail = true
    backend(null, {
      'GET /dashboard': () => (fail ? errorEnvelope(503, 'service_unavailable', 'The database is unavailable.') : json(busyWorkspace())),
    })
    renderSignedIn('/')

    expect(await screen.findByText('The database is unavailable.')).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Workspace summary' })).toBeInTheDocument()
  })
})
