import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as documentsApi from '../services/documents'
import { TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

vi.mock('../services/documents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/documents')>()),
  uploadDocument: vi.fn(),
}))

const KB = {
  id: 'kb-1',
  name: 'Company Policies',
  description: 'HR handbook',
  document_count: 1,
  status_counts: { uploaded: 1 },
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
}

function doc(overrides: Partial<documentsApi.DocumentItem> = {}): documentsApi.DocumentItem {
  return {
    id: 'doc-1',
    knowledge_base_id: 'kb-1',
    filename: 'leave_policy.pdf',
    extension: '.pdf',
    content_type: 'application/pdf',
    size_bytes: 245_760,
    status: 'uploaded',
    error_message: null,
    page_count: null,
    chunk_count: 0,
    created_at: '2026-09-25T10:00:00Z',
    processing_started_at: null,
    processed_at: null,
    ...overrides,
  }
}

const UPLOAD_CONFIG = {
  max_file_size: 1024 * 1024,
  supported_types: [
    { extension: '.pdf', content_type: 'application/pdf', label: 'PDF' },
    { extension: '.md', content_type: 'text/markdown', label: 'Markdown' },
  ],
}

function backend(overrides: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /knowledge-bases/kb-1': () => json(KB),
    'GET /knowledge-bases/kb-1/documents': () => json([doc()]),
    'GET /documents/upload-config': () => json(UPLOAD_CONFIG),
    ...overrides,
  })
}

describe('KnowledgeBaseDetailPage', () => {
  beforeEach(() => {
    vi.mocked(documentsApi.uploadDocument).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows indexed documents with chunk and page counts', async () => {
    backend({
      'GET /knowledge-bases/kb-1/documents': () =>
        json([doc({ status: 'completed', chunk_count: 42, page_count: 12, processed_at: '2026-09-25T10:01:00Z' })]),
    })

    renderSignedIn('/knowledge-bases/kb-1')

    expect(await screen.findByRole('heading', { name: 'Company Policies' })).toBeInTheDocument()
    const row = (await screen.findByText('leave_policy.pdf')).closest('tr')!
    expect(within(row).getByText('Indexed')).toBeInTheDocument()
    expect(within(row).getByText('PDF · 42 chunks · 12 pages')).toBeInTheDocument()
    expect(within(row).getByText('240 KB')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  it('polls while documents are processing and stops when they finish', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const responses = [
      [doc({ status: 'uploaded' })],
      [doc({ status: 'processing' })],
      [doc({ status: 'completed', chunk_count: 7, page_count: 2 })],
    ]
    let calls = 0
    backend({ 'GET /knowledge-bases/kb-1/documents': () => json(responses[Math.min(calls++, 2)]) })
    renderSignedIn('/knowledge-bases/kb-1')

    const row = (await screen.findByText('leave_policy.pdf')).closest('tr')!
    expect(within(row).getByText('Queued')).toBeInTheDocument()
    expect(screen.getByText(/\(1 file · 1 processing\)/)).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(2000)
    expect(await within(row).findByText('Processing')).toBeInTheDocument()
    expect(within(row).getByText('PDF · Extracting and indexing…')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(2000)
    expect(await within(row).findByText('Indexed')).toBeInTheDocument()
    expect(within(row).getByText('PDF · 7 chunks · 2 pages')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(10_000)
    expect(calls).toBe(3) // polling stopped once nothing was pending
  })

  it('shows live embedding progress from the worker', async () => {
    const progress = { stage: 'embedding' as const, done: 128, total: 400, updated_at: 1 }
    backend({ 'GET /knowledge-bases/kb-1/documents': () => json([doc({ status: 'processing', progress })]) })
    renderSignedIn('/knowledge-bases/kb-1')

    const row = (await screen.findByText('leave_policy.pdf')).closest('tr')!
    expect(within(row).getByText('PDF · Embedding 128/400 chunks')).toBeInTheDocument()
    const bar = within(row).getByRole('progressbar', { name: 'Embedding 128 of 400 chunks' })
    expect(bar).toHaveAttribute('aria-valuenow', '128')
    expect(bar).toHaveAttribute('aria-valuemax', '400')
  })

  it('warns when documents wait and no ingestion worker is running', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let workers = 0
    const readiness = () =>
      json({
        status: 'ready',
        checks: { database: true, pgvector: true },
        services: { redis: true, ingestion_workers: workers, ingestion_waiting: 1 },
      })
    backend({
      'GET /knowledge-bases/kb-1/documents': () => json([doc({ status: 'uploaded', created_at: new Date(Date.now() - 60_000).toISOString() })]),
      'GET /health/ready': readiness,
    })
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')

    await vi.advanceTimersByTimeAsync(2000)
    expect(await screen.findByText('Documents are waiting for an ingestion worker.')).toBeInTheDocument()

    workers = 1 // a worker was started
    await vi.advanceTimersByTimeAsync(2000)
    await waitFor(() => expect(screen.queryByText('Documents are waiting for an ingestion worker.')).not.toBeInTheDocument())
  })

  it('does not warn about workers for a document that was just uploaded', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchSpy = backend({
      'GET /knowledge-bases/kb-1/documents': () => json([doc({ status: 'uploaded', created_at: new Date().toISOString() })]),
    })
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')

    await vi.advanceTimersByTimeAsync(4000)

    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/health/ready'))).toBe(false)
    expect(screen.queryByText('Documents are waiting for an ingestion worker.')).not.toBeInTheDocument()
  })

  it('shows why processing failed and retries on request', async () => {
    const failed = doc({ status: 'failed', error_message: 'The PDF is password-protected. Remove the password and upload it again.' })
    const fetchSpy = backend({
      'GET /knowledge-bases/kb-1/documents': () => json([failed]),
      'POST /documents/doc-1/reprocess': () => json(doc({ status: 'uploaded' }), 202),
    })
    renderSignedIn('/knowledge-bases/kb-1')

    const row = (await screen.findByText('leave_policy.pdf')).closest('tr')!
    expect(within(row).getByText('Failed')).toBeInTheDocument()
    expect(within(row).getByText(/password-protected/)).toBeInTheDocument()

    await userEvent.click(within(row).getByRole('button', { name: 'Retry processing leave_policy.pdf' }))

    expect(await within(row).findByText('Queued')).toBeInTheDocument()
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).endsWith('/reprocess') && init?.method === 'POST')).toBe(true)
  })

  it('rejects unsupported and oversized files before uploading', async () => {
    backend()
    const user = userEvent.setup({ applyAccept: false })
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')

    await user.upload(screen.getByTestId('file-input'), [
      new File(['MZ'], 'setup.exe'),
      new File([new Uint8Array(2 * 1024 * 1024)], 'huge.pdf'),
    ])

    expect(await screen.findByText('Unsupported file type. Upload one of: PDF, Markdown.')).toBeInTheDocument()
    expect(screen.getByText('File is larger than the 1.0 MB limit.')).toBeInTheDocument()
    expect(documentsApi.uploadDocument).not.toHaveBeenCalled()
  })

  it('accepts files dropped onto the upload area', async () => {
    backend()
    vi.mocked(documentsApi.uploadDocument).mockResolvedValue(doc({ id: 'doc-3', filename: 'dropped.md', extension: '.md' }))
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')
    const dropzone = screen.getByRole('button', { name: 'Choose files' }).closest('div.border-dashed')!

    fireEvent.dragOver(dropzone, { dataTransfer: { files: [], types: ['Files'] } })
    expect(dropzone.className).toContain('border-brand-500') // highlighted while dragging
    fireEvent.drop(dropzone, { dataTransfer: { files: [new File(['# Dropped'], 'dropped.md')], types: ['Files'] } })

    expect(await screen.findByText('(2 files · 2 processing)')).toBeInTheDocument()
    expect(screen.getByText('dropped.md').closest('tr')).not.toBeNull() // now a row in the documents table
    expect(documentsApi.uploadDocument).toHaveBeenCalledWith('kb-1', expect.any(File), expect.any(Object))
    expect(dropzone.className).not.toContain('border-brand-500')
  })

  it('uploads a valid file and adds it to the table', async () => {
    backend()
    vi.mocked(documentsApi.uploadDocument).mockImplementation(async (_kb, file, options) => {
      options?.onProgress?.(0.5)
      return doc({ id: 'doc-2', filename: file.name, extension: '.md', size_bytes: file.size })
    })
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')

    await userEvent.upload(screen.getByTestId('file-input'), new File(['# Notes'], 'notes.md'))

    expect(await screen.findByText('notes.md')).toBeInTheDocument()
    expect(documentsApi.uploadDocument).toHaveBeenCalledWith('kb-1', expect.any(File), expect.any(Object))
    expect(screen.getByText('(2 files · 2 processing)')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Uploads' })).not.toBeInTheDocument()
  })

  it('keeps a failed upload visible with the server’s reason', async () => {
    backend()
    const { ApiError } = await import('../services/api')
    vi.mocked(documentsApi.uploadDocument).mockRejectedValue(
      new ApiError(409, 'conflict', "This file is already in the knowledge base as 'leave_policy.pdf'.", null),
    )
    renderSignedIn('/knowledge-bases/kb-1')
    await screen.findByText('leave_policy.pdf')

    await userEvent.upload(screen.getByTestId('file-input'), new File(['%PDF-'], 'copy.pdf'))

    const uploads = await screen.findByRole('list', { name: 'Uploads' })
    expect(await within(uploads).findByText(/already in the knowledge base/)).toBeInTheDocument()
    await userEvent.click(within(uploads).getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('list', { name: 'Uploads' })).not.toBeInTheDocument()
  })

  it('deletes a document after confirmation', async () => {
    const fetchSpy = backend({ 'DELETE /documents/doc-1': () => json(null, 204) })
    renderSignedIn('/knowledge-bases/kb-1')

    await userEvent.click(await screen.findByRole('button', { name: 'Delete leave_policy.pdf' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete document?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete document' }))

    await waitFor(() => expect(screen.queryByText('leave_policy.pdf')).not.toBeInTheDocument())
    expect(fetchSpy.mock.calls.some(([url, init]) => String(url).endsWith('/documents/doc-1') && init?.method === 'DELETE')).toBe(true)
  })

  it('deletes the knowledge base and returns to the list', async () => {
    backend({
      'DELETE /knowledge-bases/kb-1': () => json(null, 204),
      'GET /knowledge-bases': () => json([]),
    })
    renderSignedIn('/knowledge-bases/kb-1')

    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete knowledge base' }))

    expect(await screen.findByText('No knowledge bases yet')).toBeInTheDocument()
  })

  it('explains when the knowledge base does not exist', async () => {
    backend({ 'GET /knowledge-bases/kb-1': () => errorEnvelope(404, 'not_found', 'Knowledge base not found.') })

    renderSignedIn('/knowledge-bases/kb-1')

    expect(await screen.findByText(/does not exist or you do not have access/)).toBeInTheDocument()
  })
})
