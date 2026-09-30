import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const KB = {
  id: 'kb-1',
  name: 'Company Policies',
  description: 'HR handbook and leave policy',
  document_count: 3,
  status_counts: { completed: 2, failed: 1 },
  passage_count: 48,
  total_bytes: 3 * 1024 * 1024,
  conversation_count: 5,
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
  last_activity_at: '2026-09-28T10:00:00Z',
}

const UPLOAD_CONFIG = {
  max_file_size: 25 * 1024 * 1024,
  supported_types: [{ extension: '.pdf', content_type: 'application/pdf', label: 'PDF' }],
}

describe('KnowledgeBasesPage', () => {
  it('lists the user’s knowledge bases', async () => {
    mockFetch({ 'GET /auth/me': () => json(TEST_USER), 'GET /knowledge-bases': () => json([KB]) })

    renderSignedIn('/knowledge-bases')

    const card = await screen.findByRole('link', { name: /Company Policies/ })
    expect(card).toHaveAttribute('href', '/knowledge-bases/kb-1')
    expect(within(card).getByText('HR handbook and leave policy')).toBeInTheDocument()
    const stat = (label: string) => within(card).getByText(label).nextElementSibling
    expect(stat('Documents')).toHaveTextContent('3')
    expect(stat('Passages')).toHaveTextContent('48')
    expect(stat('Chats')).toHaveTextContent('5')
    expect(within(card).getByText(/3\.0 MB/)).toBeInTheDocument()
    expect(within(card).getByText('· 1 failed')).toBeInTheDocument()
  })

  it('shows an empty state for new users', async () => {
    mockFetch({ 'GET /auth/me': () => json(TEST_USER), 'GET /knowledge-bases': () => json([]) })

    renderSignedIn('/knowledge-bases')

    expect(await screen.findByText('No knowledge bases yet')).toBeInTheDocument()
  })

  it('creates a knowledge base and opens it', async () => {
    const created = { ...KB, id: 'kb-new', name: 'Python Docs', description: null, document_count: 0, status_counts: {} }
    const fetchSpy = mockFetch({
      'GET /auth/me': () => json(TEST_USER),
      'GET /knowledge-bases': () => json([]),
      'POST /knowledge-bases': () => json(created, 201),
      'GET /knowledge-bases/kb-new': () => json(created),
      'GET /knowledge-bases/kb-new/documents': () => json([]),
      'GET /documents/upload-config': () => json(UPLOAD_CONFIG),
    })
    renderSignedIn('/knowledge-bases')

    await userEvent.click(await screen.findByRole('button', { name: 'New knowledge base' }))
    const dialog = screen.getByRole('dialog', { name: 'New knowledge base' })
    await userEvent.type(within(dialog).getByLabelText('Name'), '  Python Docs ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(await screen.findByRole('heading', { name: 'Python Docs' })).toBeInTheDocument()
    const [, init] = fetchSpy.mock.calls.find(([, i]) => i?.method === 'POST')!
    expect(JSON.parse(String(init?.body))).toEqual({ name: 'Python Docs', description: null })
  })

  it('shows a name conflict next to the field', async () => {
    mockFetch({
      'GET /auth/me': () => json(TEST_USER),
      'GET /knowledge-bases': () => json([KB]),
      'POST /knowledge-bases': () => errorEnvelope(409, 'conflict', 'You already have a knowledge base with this name.'),
    })
    renderSignedIn('/knowledge-bases')

    await userEvent.click(await screen.findByRole('button', { name: 'New knowledge base' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Name'), 'company policies')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(await within(dialog).findByText('You already have a knowledge base with this name.')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('searches as you type and changes the sort order', async () => {
    const spy = mockFetch({ 'GET /auth/me': () => json(TEST_USER), 'GET /knowledge-bases': () => json([KB]) })
    renderSignedIn('/knowledge-bases')
    await screen.findByRole('link', { name: /Company Policies/ })
    const requests = () => spy.mock.calls.map(([input]) => String(input)).filter((url) => url.includes('/knowledge-bases'))

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search knowledge bases' }), 'policy')
    await waitFor(() => expect(requests().at(-1)).toContain('search=policy'))
    expect(requests().filter((url) => url.includes('search=p'))).toHaveLength(1) // once, after typing pauses

    await userEvent.selectOptions(screen.getByLabelText('Sort by'), 'name')
    await waitFor(() => expect(requests().at(-1)).toMatch(/search=policy.*sort=name/))
  })

  it('says when nothing matches the search', async () => {
    const spy = mockFetch({
      'GET /auth/me': () => json(TEST_USER),
      'GET /knowledge-bases': () => json(spy.mock.calls.some(([input]) => String(input).includes('search=')) ? [] : [KB]),
    })
    renderSignedIn('/knowledge-bases')
    await screen.findByRole('link', { name: /Company Policies/ })

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search knowledge bases' }), 'zzz')

    expect(await screen.findByText('No knowledge bases match “zzz”.')).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search knowledge bases' })).toBeInTheDocument()
  })
})
