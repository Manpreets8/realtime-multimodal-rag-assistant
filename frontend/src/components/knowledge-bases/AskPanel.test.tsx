import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { AnswerResponse } from '../../services/rag'
import { TEST_USER, errorEnvelope, json, mockFetch } from '../../test/mockFetch'
import { renderSignedIn } from '../../test/renderApp'

const KB = {
  id: 'kb-1',
  name: 'Company Policies',
  description: null,
  document_count: 2,
  status_counts: { completed: 2 },
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
}

const LEAVE = 'All full-time employees are entitled to 18 days of paid annual leave per calendar year.'
const QUOTE = 'entitled to 18 days of paid annual leave'

const source = (number: number, filename: string, page: number | null, section: string | null, content: string) => ({
  number,
  chunk_id: `c${number}`,
  document_id: `d${number}`,
  knowledge_base_id: 'kb-1',
  filename,
  page_number: page,
  section,
  content,
  rerank_score: 3 - number,
  similarity: 0.7,
})

const ANSWER_TEXT = 'Full-time employees get 18 days of paid annual leave. Up to 5 days carry over.'

const GROUNDED: AnswerResponse = {
  question: 'How much annual leave?',
  answer: ANSWER_TEXT,
  answer_type: 'knowledge_base',
  grounded: true,
  citations: [
    {
      source_number: 1,
      document_id: 'd1',
      filename: 'handbook.pdf',
      page_number: 2,
      section: null,
      quotes: [{ text: QUOTE, start: LEAVE.indexOf(QUOTE), end: LEAVE.indexOf(QUOTE) + QUOTE.length }],
      answer_spans: [[0, 53]],
    },
    {
      source_number: 2,
      document_id: 'd2',
      filename: 'policy.docx',
      page_number: null,
      section: 'Leave > Carry over',
      quotes: [{ text: 'Up to 5 unused days carry over', start: null, end: null }],
      answer_spans: [[53, ANSWER_TEXT.length]],
    },
  ],
  sources: [
    source(1, 'handbook.pdf', 2, null, LEAVE),
    source(2, 'policy.docx', null, 'Leave > Carry over', 'Carry over: up to five unused days.'),
    source(3, 'security.docx', null, 'Passwords', 'Passwords need 14 characters.'),
  ],
  model: 'claude-opus-5',
  usage: { input_tokens: 1450, output_tokens: 38 },
  truncated: false,
  retrieval: { vector_candidates: 5, keyword_candidates: 3, filtered_out: 1, reranked: true },
  timings_ms: { retrieval: 61.2, rerank: 74.9, llm: 2310.4, total: 2449.1 },
}

const CONTEXT = {
  chunk: { id: 'c1', chunk_index: 4, page_number: 2, section: null, content: LEAVE },
  before: [{ id: 'c0', chunk_index: 3, page_number: 1, section: null, content: 'Welcome to Acme.' }],
  after: [{ id: 'c2', chunk_index: 5, page_number: 3, section: null, content: 'Remote work rules.' }],
  document: {
    id: 'd1',
    knowledge_base_id: 'kb-1',
    filename: 'handbook.pdf',
    extension: '.pdf',
    content_type: 'application/pdf',
    size_bytes: 1000,
    status: 'completed',
    error_message: null,
    page_count: 4,
    chunk_count: 8,
    created_at: '2026-09-25T10:00:00Z',
    processing_started_at: null,
    processed_at: null,
  },
}

function backend(answer: () => Response, extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /knowledge-bases/kb-1': () => json(KB),
    'GET /knowledge-bases/kb-1/documents': () => json([]),
    'GET /documents/upload-config': () => json({ max_file_size: 1, supported_types: [] }),
    'POST /rag/answer': answer,
    ...extra,
  })
}

async function ask(question: string) {
  await userEvent.type(await screen.findByLabelText('Ask a question about this knowledge base'), question)
  await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('Ask tab', () => {
  it('shows the answer with inline citation markers after the sentences they support', async () => {
    const fetchSpy = backend(() => json(GROUNDED))
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')

    await ask('How much annual leave?')

    const answer = await screen.findByRole('region', { name: 'Answer' })
    expect(within(answer).getByText('Answered from your documents')).toBeInTheDocument()
    const first = within(answer).getByRole('button', { name: 'Source 1: handbook.pdf, Page 2' })
    const second = within(answer).getByRole('button', { name: 'Source 2: policy.docx, Leave > Carry over' })
    // Each marker sits right after the text span it supports.
    expect(first.closest('sup')?.previousSibling?.textContent).toBe('Full-time employees get 18 days of paid annual leave.')
    expect(second.closest('sup')?.previousSibling?.textContent).toBe(' Up to 5 days carry over.')
    expect(within(answer).getByRole('list', { name: 'Cited sources' })).toHaveTextContent('handbook.pdf — Page 2')
    expect(within(answer).getByText('(not cited)')).toBeInTheDocument() // security.docx was context only
    expect(screen.getByTestId('answer-diagnostics')).toHaveTextContent('Model claude-opus-5 · 1450 in / 38 out tokens · reranked')
    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/rag/answer'))!
    expect(JSON.parse(String(init?.body))).toEqual({ question: 'How much annual leave?', knowledge_base_ids: ['kb-1'] })
  })

  it('opens the cited passage in context with the quote highlighted', async () => {
    backend(() => json(GROUNDED), { 'GET /chunks/c1': () => json(CONTEXT) })
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')
    await ask('How much annual leave?')

    await userEvent.click(await screen.findByRole('button', { name: 'Source 1: handbook.pdf, Page 2' }))

    const viewer = await screen.findByRole('dialog', { name: 'handbook.pdf' })
    expect(within(viewer).getByText('Source 1')).toBeInTheDocument()
    const cited = await within(viewer).findByTestId('cited-chunk')
    expect(within(cited).getByText(QUOTE, { selector: 'mark' })).toBeInTheDocument()
    expect(within(viewer).getAllByTestId('neighbour-chunk').map((n) => n.textContent)).toEqual([
      'Welcome to Acme.',
      'Remote work rules.',
    ])
    expect(within(viewer).getByRole('button', { name: 'Open page 2' })).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('lists quotes it could not locate instead of guessing a highlight, and hides "Open page" for non-PDFs', async () => {
    const docxContext = {
      ...CONTEXT,
      chunk: { ...CONTEXT.chunk, id: 'c2', content: 'Carry over: up to five unused days.' },
      document: { ...CONTEXT.document, filename: 'policy.docx', extension: '.docx' },
    }
    backend(() => json(GROUNDED), { 'GET /chunks/c2': () => json(docxContext) })
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')
    await ask('How much annual leave?')

    await userEvent.click(await screen.findByRole('button', { name: /^Source 2:/ }))

    const viewer = await screen.findByRole('dialog', { name: 'policy.docx' })
    const cited = await within(viewer).findByTestId('cited-chunk')
    expect(cited.querySelector('mark')).toBeNull()
    expect(within(viewer).getByText('“Up to 5 unused days carry over”')).toBeInTheDocument()
    expect(within(viewer).queryByRole('button', { name: /Open/ })).not.toBeInTheDocument()
    expect(within(viewer).getByRole('button', { name: 'Download' })).toBeInTheDocument()
  })

  it('shows the saved passage, still highlighted, when the live one no longer exists', async () => {
    backend(() => json(GROUNDED), { 'GET /chunks/c1': () => errorEnvelope(404, 'not_found', 'Source not found.') })
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')
    await ask('How much annual leave?')

    await userEvent.click(await screen.findByRole('button', { name: /^Source 1:/ }))

    const viewer = screen.getByRole('dialog')
    expect(await within(viewer).findByRole('note')).toHaveTextContent('no longer available')
    expect(within(within(viewer).getByTestId('cited-chunk')).getByText(QUOTE, { selector: 'mark' })).toBeInTheDocument()
    expect(within(viewer).queryByRole('button', { name: /Open page|Download/ })).not.toBeInTheDocument()
  })

  it('labels answers that were not found in the knowledge base', async () => {
    backend(() =>
      json({ ...GROUNDED, answer: 'Not found.', answer_type: 'not_found', grounded: false, citations: [], sources: [], model: null, usage: null }),
    )
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')

    await ask('What is the capital of France?')

    expect(await screen.findByText('Not found in the knowledge base')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Cited sources' })).not.toBeInTheDocument()
  })

  it('explains when the AI model is not configured', async () => {
    backend(() =>
      errorEnvelope(503, 'llm_not_configured', 'The AI model is not configured. Set LLM_API_KEY (an Anthropic API key) in .env and restart.'),
    )
    renderSignedIn('/knowledge-bases/kb-1?tab=ask')

    await ask('Anything')

    expect(await screen.findByRole('alert')).toHaveTextContent('Set LLM_API_KEY')
    expect(screen.getByRole('alert')).not.toHaveTextContent('ref ')
  })
})
