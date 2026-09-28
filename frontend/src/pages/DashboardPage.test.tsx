import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HEALTHY_BACKEND, TEST_USER, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

function backend(knowledgeBases: unknown[], conversations: unknown[]) {
  mockFetch({
    ...HEALTHY_BACKEND,
    'GET /auth/me': () => json(TEST_USER),
    'GET /knowledge-bases': () => json(knowledgeBases),
    'GET /conversations': () => json(conversations),
  })
}

const KB = {
  id: 'kb-1', name: 'Company Policies', description: null, document_count: 7,
  status_counts: { completed: 5, processing: 2 }, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-25T10:00:00Z',
}
const CONVERSATION = {
  id: 'c-1', title: 'How much annual leave?', knowledge_base_id: 'kb-1', knowledge_base_name: 'Company Policies',
  message_count: 4, last_message_preview: '20 days.', created_at: '2026-09-26T10:00:00Z', updated_at: new Date().toISOString(),
}

describe('Dashboard', () => {
  it('guides a new user through getting started', async () => {
    backend([], [])
    renderSignedIn('/')

    expect(await screen.findByRole('heading', { name: 'Getting started' })).toBeInTheDocument()
    expect(screen.getByText('Set up your first knowledge base to start asking questions.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create a knowledge base →' })).toHaveAttribute('href', '/knowledge-bases')
  })

  it('summarises the workspace for a returning user', async () => {
    backend([KB, { ...KB, id: 'kb-2', name: 'Engineering', document_count: 3, status_counts: { completed: 3 } }], [CONVERSATION])
    renderSignedIn('/')

    const summary = await screen.findByRole('region', { name: 'Workspace summary' })
    expect(within(summary).getByRole('link', { name: /Knowledge bases\s*2/ })).toBeInTheDocument()
    expect(within(summary).getByRole('link', { name: /Documents\s*10\s*8 indexed/ })).toBeInTheDocument()
    expect(within(summary).getByRole('link', { name: /Conversations\s*1/ })).toBeInTheDocument()

    const recent = screen.getByRole('region', { name: 'Recent conversations' })
    expect(within(recent).getByRole('link', { name: /How much annual leave\?/ })).toHaveAttribute('href', '/chat/c-1')
    expect(within(recent).getByText(/Company Policies · 4 messages ·/)).toBeInTheDocument()

    const kbs = screen.getByRole('region', { name: 'Knowledge bases' })
    expect(within(kbs).getByText('7 documents')).toBeInTheDocument()
    expect(within(kbs).getByText('· 2 processing')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Getting started' })).not.toBeInTheDocument()
  })
})
