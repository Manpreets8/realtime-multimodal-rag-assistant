import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { CompareResponse } from '../services/compare'
import { HEALTHY_BACKEND, TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const doc = (id: string, filename: string) => ({
  id,
  knowledge_base_id: 'kb-1',
  knowledge_base_name: 'Career',
  filename,
  extension: '.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
  status: 'completed',
  error_message: null,
  page_count: 2,
  chunk_count: 3,
  created_at: '2026-09-25T10:00:00Z',
  processing_started_at: null,
  processed_at: null,
})

const unit = (text: string, chunk: string, page: number) => ({ text, chunk_id: chunk, page_number: page, section: null })
const V1 = 'Led a team of 3 analysts.'
const V2 = 'Led a team of 5 analysts.'
const ANALYSIS_TEXT = '## Modified information\n- The team grew from 3 to 5 analysts.'

const RESULT: CompareResponse = {
  document_a: { id: 'd1', knowledge_base_id: 'kb-1', filename: 'Resume_v1.pdf', page_count: 2, sentences: 5, coverage: 1 },
  document_b: { id: 'd2', knowledge_base_id: 'kb-1', filename: 'Resume_v2.pdf', page_count: 2, sentences: 6, coverage: 1 },
  differences: {
    counts: { added: 1, removed: 0, modified: 1, common: 4 },
    overlap: 0.7273,
    added: [unit('Certified AWS Machine Learning Specialist.', 'c-b2', 2)],
    removed: [],
    modified: [{ before: unit(V1, 'c-a1', 1), after: unit(V2, 'c-b1', 1), similarity: 0.96 }],
    common: [unit('Based in London.', 'c-b2', 2)],
    listed_limit: 200,
  },
  analysis: {
    text: ANALYSIS_TEXT,
    citations: [
      { source_number: 1, document_id: 'd1', filename: 'Resume_v1.pdf', page_number: 1, section: null, quotes: [{ text: V1, start: 0, end: V1.length }], answer_spans: [[24, ANALYSIS_TEXT.length]] },
      { source_number: 2, document_id: 'd2', filename: 'Resume_v2.pdf', page_number: 1, section: null, quotes: [{ text: V2, start: 0, end: V2.length }], answer_spans: [[24, ANALYSIS_TEXT.length]] },
    ],
    sources: [
      { number: 1, chunk_id: 'c-a1', document_id: 'd1', knowledge_base_id: 'kb-1', filename: 'Resume_v1.pdf', page_number: 1, section: null, content: V1 },
      { number: 2, chunk_id: 'c-b1', document_id: 'd2', knowledge_base_id: 'kb-1', filename: 'Resume_v2.pdf', page_number: 1, section: null, content: V2 },
    ],
    cited_documents: ['d1', 'd2'],
    citation_check: { cited_sources: 2, quotes: 2, verified_quotes: 2, rejected: 0 },
    model: 'claude-opus-5',
    usage: { input_tokens: 900, output_tokens: 60 },
    truncated: false,
  },
  analysis_unavailable: null,
  timings_ms: { diff: 2, llm: 4100, total: 4120 },
}

function backend(compare: () => Response = () => json(RESULT)) {
  return mockFetch({
    ...HEALTHY_BACKEND,
    'GET /auth/me': () => json(TEST_USER),
    'GET /documents': () => json({ items: [doc('d1', 'Resume_v1.pdf'), doc('d2', 'Resume_v2.pdf')], total: 2 }),
    'POST /documents/compare': compare,
  })
}

async function documentsLoaded() {
  await screen.findAllByRole('option', { name: 'Resume_v2.pdf — Career' })
}

async function choose() {
  await documentsLoaded()
  await userEvent.selectOptions(screen.getByLabelText('Original (A)'), 'd1')
  await userEvent.selectOptions(screen.getByLabelText('Revised (B)'), 'd2')
  await userEvent.click(screen.getByRole('button', { name: 'Compare' }))
}

describe('Compare documents', () => {
  it('is reached from the Documents page and compares only when asked', async () => {
    const fetchSpy = backend()
    renderSignedIn('/documents')

    await userEvent.click(await screen.findByRole('link', { name: 'Compare documents' }))
    expect(await screen.findByRole('heading', { name: 'Compare documents' })).toBeInTheDocument()
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/documents/compare'))).toBe(false)

    await choose()

    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/documents/compare'))!
    expect(JSON.parse(String(init!.body))).toEqual({ document_a_id: 'd1', document_b_id: 'd2' })
    const counts = await screen.findByTestId('comparison-counts')
    expect(counts).toHaveTextContent('Identical text73%')
    expect(counts).toHaveTextContent('Added1')
    expect(counts).toHaveTextContent('Modified1')
  })

  it('shows the AI analysis citing both documents, and opens exact differences in their document', async () => {
    backend()
    renderSignedIn('/documents/compare')
    await choose()

    const cited = await screen.findByRole('list', { name: 'Cited sources' })
    expect(cited).toHaveTextContent('Resume_v1.pdf — Page 1')
    expect(cited).toHaveTextContent('Resume_v2.pdf — Page 1')
    expect(screen.queryByTestId('uncited-document')).not.toBeInTheDocument()
    expect(screen.getByText('Modified information').closest('p')).toHaveClass('font-semibold')

    const modified = screen.getByText('Modified', { selector: 'summary' }).closest('details')!
    expect(within(modified).getByText(V1).tagName).toBe('DEL')
    expect(within(modified).getByText(V2).tagName).toBe('INS')
    await userEvent.click(screen.getByRole('button', { name: 'Open added sentence 1 in Resume_v2.pdf' }))
    expect(await screen.findByRole('dialog', { name: 'Resume_v2.pdf' })).toBeInTheDocument()
  })

  it('warns about partial coverage and an uncited document, and explains a missing analysis', async () => {
    const partial = {
      ...RESULT,
      document_a: { ...RESULT.document_a, coverage: 0.42 },
      analysis: { ...RESULT.analysis!, cited_documents: ['d2'] },
    }
    backend(() => json(partial))
    const { unmount } = renderSignedIn('/documents/compare')
    await choose()
    expect(await screen.findByTestId('partial-coverage')).toHaveTextContent('Resume_v1.pdf is long: the AI read 42% of it')
    expect(screen.getByTestId('uncited-document')).toHaveTextContent('doesn\'t cite Resume_v1.pdf')
    unmount()

    backend(() => json({ ...RESULT, analysis: null, analysis_unavailable: 'The AI model is not configured (set LLM_API_KEY); the exact text differences are shown.' }))
    renderSignedIn('/documents/compare')
    await choose()
    expect(await screen.findByTestId('analysis-unavailable')).toHaveTextContent('not configured')
    expect(screen.getByText('Added in B')).toBeInTheDocument()
  })

  it('blocks comparing a document with itself and shows API errors', async () => {
    backend(() => errorEnvelope(409, 'conflict', 'Resume_v2.pdf is still being processed; only indexed documents can be compared.'))
    renderSignedIn('/documents/compare?a=d1&b=d1')

    await documentsLoaded()
    expect(screen.getByRole('alert')).toHaveTextContent('Choose two different documents.')
    expect(screen.getByRole('button', { name: 'Compare' })).toBeDisabled()

    await userEvent.selectOptions(screen.getByLabelText('Revised (B)'), 'd2')
    await userEvent.click(screen.getByRole('button', { name: 'Compare' }))
    expect(await screen.findByText(/still being processed/)).toBeInTheDocument()
  })
})
