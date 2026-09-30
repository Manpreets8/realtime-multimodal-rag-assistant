import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { HEALTHY_BACKEND, TEST_USER, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

function doc(overrides: object) {
  return {
    id: 'd-1',
    knowledge_base_id: 'kb-1',
    knowledge_base_name: 'Company Policies',
    filename: 'leave.pdf',
    extension: '.pdf',
    content_type: 'application/pdf',
    size_bytes: 204_800,
    status: 'completed',
    error_message: null,
    page_count: 12,
    chunk_count: 40,
    created_at: '2026-09-28T10:00:00Z',
    processing_started_at: null,
    processed_at: '2026-09-28T10:01:00Z',
    progress: null,
    ...overrides,
  }
}

const READY = doc({})
const FAILED = doc({ id: 'd-2', filename: 'scan.pdf', status: 'failed', error_message: 'No text found.', chunk_count: 0 })

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    ...HEALTHY_BACKEND,
    'GET /auth/me': () => json(TEST_USER),
    'GET /documents': () => json({ items: [READY, FAILED], total: 2 }),
    ...extra,
  })
}

function lastListUrl(spy: ReturnType<typeof mockFetch>): string {
  return String(spy.mock.calls.filter(([input]) => String(input).includes('/documents?')).at(-1)![0])
}

describe('DocumentsPage', () => {
  it('lists documents from every knowledge base', async () => {
    backend()
    renderSignedIn('/documents')

    const ready = (await screen.findByRole('link', { name: 'leave.pdf' })).closest('li')!
    expect(screen.getByRole('link', { name: 'leave.pdf' })).toHaveAttribute('href', '/knowledge-bases/kb-1')
    expect(within(ready).getByText(/Company Policies · 200 KB · 12 pages · 40 passages/)).toBeInTheDocument()
    expect(within(ready).getByText('Indexed')).toBeInTheDocument()
    const failed = screen.getByRole('link', { name: 'scan.pdf' }).closest('li')!
    expect(within(failed).getByText('No text found.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Documents' })).toHaveAttribute('aria-current', 'page')
  })

  it('filters by status and searches by filename', async () => {
    const spy = backend()
    renderSignedIn('/documents')
    await screen.findByRole('link', { name: 'leave.pdf' })

    await userEvent.click(screen.getByRole('button', { name: 'Failed' }))
    expect(screen.getByRole('button', { name: 'Failed' })).toHaveAttribute('aria-pressed', 'true')
    expect(lastListUrl(spy)).toContain('status=failed')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search documents by filename' }), 'scan')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByRole('link', { name: 'scan.pdf' })
    expect(lastListUrl(spy)).toMatch(/status=failed.*search=scan|search=scan.*status=failed/)
  })

  it('retries a failed document and confirms with a notification', async () => {
    backend({ 'POST /documents/d-2/reprocess': () => json({ ...FAILED, status: 'uploaded', error_message: null }, 202) })
    renderSignedIn('/documents')

    await userEvent.click(await screen.findByRole('button', { name: 'Retry scan.pdf' }))

    expect(await screen.findByRole('status', { name: '' })).toHaveTextContent('Processing “scan.pdf” again.')
    const row = screen.getByRole('link', { name: 'scan.pdf' }).closest('li')!
    expect(within(row).getByText('Queued')).toBeInTheDocument()
  })

  it('deletes after confirmation', async () => {
    let deleted = false
    backend({
      'GET /documents': () => json(deleted ? { items: [FAILED], total: 1 } : { items: [READY, FAILED], total: 2 }),
      'DELETE /documents/d-1': () => {
        deleted = true
        return json(null, 204)
      },
    })
    renderSignedIn('/documents')

    await userEvent.click(await screen.findByRole('button', { name: 'Delete leave.pdf' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete this document?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete document' }))

    expect(await screen.findByText('Deleted “leave.pdf”.')).toBeInTheDocument()
    await screen.findByRole('link', { name: 'scan.pdf' })
    expect(screen.queryByRole('link', { name: 'leave.pdf' })).not.toBeInTheDocument()
  })

  it('explains an empty workspace', async () => {
    backend({ 'GET /documents': () => json({ items: [], total: 0 }) })
    renderSignedIn('/documents')

    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to knowledge bases →' })).toHaveAttribute('href', '/knowledge-bases')
  })
})
