import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { SearchResponse } from '../../services/retrieval'
import { TEST_USER, errorEnvelope, json, mockFetch } from '../../test/mockFetch'
import { renderSignedIn } from '../../test/renderApp'

const KB = {
  id: 'kb-1',
  name: 'Company Policies',
  description: null,
  document_count: 1,
  status_counts: { completed: 1 },
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
}

const RESPONSE: SearchResponse = {
  query: 'annual leave days',
  mode: 'hybrid',
  results: [
    {
      chunk_id: 'c1',
      document_id: 'd1',
      knowledge_base_id: 'kb-1',
      filename: 'handbook.pdf',
      chunk_index: 3,
      page_number: 2,
      section: null,
      content: 'Employees receive 18 days of paid annual leave.',
      score: 0.032,
      similarity: 0.71,
      keyword_score: 0.2,
      vector_rank: 1,
      keyword_rank: 1,
    },
    {
      chunk_id: 'c2',
      document_id: 'd2',
      knowledge_base_id: 'kb-1',
      filename: 'policy.docx',
      chunk_index: 0,
      page_number: null,
      section: 'Leave Policy > Carry over',
      content: 'Up to 5 unused days carry over.',
      score: 0.016,
      similarity: 0.58,
      keyword_score: null,
      vector_rank: 2,
      keyword_rank: null,
    },
  ],
  vector_candidates: 4,
  keyword_candidates: 1,
  filtered_out: 2,
  similarity_threshold: 0.5,
  timings_ms: { embedding: 11.2, vector_search: 3.4, keyword_search: 1.9, fusion: 0.02, fetch: 2.5, total: 19.8 },
}

function backend(searchHandler: () => Response) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /knowledge-bases/kb-1': () => json(KB),
    'GET /knowledge-bases/kb-1/documents': () => json([]),
    'GET /documents/upload-config': () => json({ max_file_size: 1, supported_types: [] }),
    'POST /retrieval/search': searchHandler,
  })
}

describe('Search tab', () => {
  it('highlights whole words that start with a query term, never fragments inside words', async () => {
    const hit = { ...RESPONSE.results[0], content: 'Hotels: the budget covers hotel stays; budgeting is separate.' }
    backend(() => json({ ...RESPONSE, query: 'hotel budget get', results: [hit] }))
    renderSignedIn('/knowledge-bases/kb-1?tab=search')

    await userEvent.type(await screen.findByLabelText('Search this knowledge base'), 'hotel budget get')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    const item = within(await screen.findByRole('list')).getByRole('listitem')
    const marked = [...item.querySelectorAll('mark')].map((mark) => mark.textContent)
    expect(marked).toEqual(['Hotels', 'budget', 'hotel', 'budgeting'])  // "get" never matches inside "budget"
  })

  it('opens from the URL and shows ranked results with scores, locations and timings', async () => {
    const fetchSpy = backend(() => json(RESPONSE))
    renderSignedIn('/knowledge-bases/kb-1?tab=search')

    await userEvent.type(await screen.findByLabelText('Search this knowledge base'), 'annual leave days')
    await userEvent.click(screen.getByRole('radio', { name: 'Semantic' }))
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    const results = await screen.findByRole('list')
    const [first, second] = within(results).getAllByRole('listitem')
    expect(within(first).getByText('handbook.pdf')).toBeInTheDocument()
    expect(within(first).getByText('Page 2')).toBeInTheDocument()
    expect(within(first).getByText('Similarity 0.71')).toBeInTheDocument()
    expect(within(first).getByText('Keyword #1')).toBeInTheDocument()
    expect(within(first).getAllByText('annual', { selector: 'mark' })).toHaveLength(1)
    expect(within(second).getByText('Leave Policy > Carry over')).toBeInTheDocument()
    expect(within(second).queryByText(/Keyword #/)).not.toBeInTheDocument()
    expect(screen.getByTestId('search-diagnostics')).toHaveTextContent(
      '4 semantic + 1 keyword candidates, 2 below threshold · Embed query 11 ms · Vector 3.4 ms',
    )

    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/retrieval/search'))!
    expect(JSON.parse(String(init?.body))).toEqual({ query: 'annual leave days', knowledge_base_ids: ['kb-1'], mode: 'vector' })
  })

  it('explains an empty result caused by the similarity threshold', async () => {
    backend(() => json({ ...RESPONSE, results: [], filtered_out: 3 }))
    renderSignedIn('/knowledge-bases/kb-1?tab=search')

    await userEvent.type(await screen.findByLabelText('Search this knowledge base'), 'sourdough')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('No relevant passages found')).toBeInTheDocument()
    expect(screen.getByText('3 candidates were below the similarity threshold (0.5).')).toBeInTheDocument()
  })

  it('shows API errors', async () => {
    backend(() => errorEnvelope(404, 'not_found', 'One or more knowledge bases were not found.'))
    renderSignedIn('/knowledge-bases/kb-1?tab=search')

    await userEvent.type(await screen.findByLabelText('Search this knowledge base'), 'leave')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('One or more knowledge bases were not found.')
  })

  it('switches between the documents and search tabs', async () => {
    backend(() => json(RESPONSE))
    renderSignedIn('/knowledge-bases/kb-1')

    expect(await screen.findByRole('tab', { name: 'Documents', selected: true })).toBeInTheDocument()
    expect(screen.getByText('Upload documents')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Search' }))

    expect(screen.getByRole('tab', { name: 'Search', selected: true })).toBeInTheDocument()
    expect(screen.getByRole('search')).toBeInTheDocument()
    expect(screen.queryByText('Upload documents')).not.toBeInTheDocument()
    expect(screen.getByText(/No documents are indexed yet/)).toBeInTheDocument()
  })
})

describe('Search result viewer', () => {
  it('opens a result in context', async () => {
    mockFetch({
      'GET /auth/me': () => json(TEST_USER),
      'GET /knowledge-bases/kb-1': () => json(KB),
      'GET /knowledge-bases/kb-1/documents': () => json([]),
      'GET /documents/upload-config': () => json({ max_file_size: 1, supported_types: [] }),
      'POST /retrieval/search': () => json(RESPONSE),
      'GET /chunks/c1': () =>
        json({
          chunk: { id: 'c1', chunk_index: 3, page_number: 2, section: null, content: RESPONSE.results[0].content },
          before: [],
          after: [],
          document: { id: 'd1', filename: 'handbook.pdf', extension: '.pdf' },
        }),
    })
    renderSignedIn('/knowledge-bases/kb-1?tab=search')
    await userEvent.type(await screen.findByLabelText('Search this knowledge base'), 'annual leave')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    await userEvent.click(await screen.findByRole('button', { name: 'View result 1 in context' }))

    const viewer = await screen.findByRole('dialog', { name: 'handbook.pdf' })
    expect(within(viewer).getByText('Result 1')).toBeInTheDocument()
    expect(await within(viewer).findByTestId('cited-chunk')).toHaveTextContent('Employees receive 18 days')
    expect(within(viewer).getByRole('button', { name: 'Open page 2' })).toBeInTheDocument()
  })
})
