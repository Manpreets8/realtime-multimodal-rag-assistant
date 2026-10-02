import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { SearchHit, SearchResponse } from '../services/retrieval'
import { HEALTHY_BACKEND, TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const kb = (id: string, name: string) => ({
  id,
  name,
  description: null,
  document_count: 1,
  status_counts: { completed: 1 },
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
})

const hit = (id: string, filename: string, kbId: string, relevance: SearchHit['relevance'], extra: Partial<SearchHit> = {}): SearchHit => ({
  chunk_id: id,
  document_id: `d-${id}`,
  knowledge_base_id: kbId,
  filename,
  chunk_index: 0,
  page_number: null,
  section: null,
  content: `Passage about expense approvals in ${filename}.`,
  score: 0.03,
  similarity: 0.7,
  keyword_score: null,
  vector_rank: 1,
  keyword_rank: null,
  rerank_score: relevance === 'high' ? 8.2 : relevance === 'medium' ? 1.1 : -9.4,
  relevance,
  ...extra,
})

const RESPONSE: SearchResponse = {
  query: 'expense approvals',
  mode: 'hybrid',
  results: [
    hit('c1', 'Expense Policy.pdf', 'kb-1', 'high', { page_number: 12 }),
    hit('c2', 'Finance Notes.md', 'kb-2', 'medium', { section: 'Approvals' }),
    hit('c3', 'Office Guide.pdf', 'kb-1', 'low', { page_number: 3 }),
  ],
  vector_candidates: 8,
  keyword_candidates: 4,
  filtered_out: 0,
  similarity_threshold: 0.5,
  reranker: 'Xenova/ms-marco-MiniLM-L-6-v2',
  timings_ms: { total: 120 },
}

function backend(searchHandler: () => Response = () => json(RESPONSE)) {
  return mockFetch({
    ...HEALTHY_BACKEND,
    'GET /auth/me': () => json(TEST_USER),
    'GET /knowledge-bases': () => json([kb('kb-1', 'Policies'), kb('kb-2', 'Finance')]),
    'POST /retrieval/search': searchHandler,
  })
}

describe('Search My Knowledge', () => {
  it('searches every knowledge base and groups results by relevance', async () => {
    const fetchSpy = backend()
    renderSignedIn('/search')

    await userEvent.click(await screen.findByRole('link', { name: 'Search' }))
    await userEvent.type(screen.getByLabelText('What are you looking for?'), 'expense approvals')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByTestId('result-count')).toHaveTextContent('2 relevant results')
    expect(screen.queryByTestId('searched-for')).not.toBeInTheDocument() // the query was already a topic
    const [first, second] = within(screen.getAllByRole('list')[0]).getAllByRole('listitem')
    expect(first).toHaveTextContent('Expense Policy.pdf')
    expect(first).toHaveTextContent('Page 12 · Policies')
    expect(within(first).getByText('Relevance: High')).toHaveAttribute('title', expect.stringContaining('8.20'))
    expect(second).toHaveTextContent('Approvals · Finance')
    expect(within(second).getByText('Relevance: Medium')).toBeInTheDocument()
    // Low relevance results are collapsed, not hidden.
    expect(screen.getByText('1 less relevant result')).toBeInTheDocument()
    expect(screen.getByText('Office Guide.pdf')).not.toBeVisible()

    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/retrieval/search'))!
    expect(JSON.parse(String(init!.body))).toEqual({
      query: 'expense approvals',
      knowledge_base_ids: [],
      limit: 20,
      options: { rerank: true, topic: true },
    })
  })

  it('can search one knowledge base, keeps the search in the URL and opens a source', async () => {
    const fetchSpy = backend()
    renderSignedIn('/search?q=expense%20approvals&kb=kb-2')

    expect(await screen.findByTestId('result-count')).toHaveTextContent('2 relevant results in Finance')
    expect(screen.getByLabelText('What are you looking for?')).toHaveValue('expense approvals')
    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/retrieval/search'))!
    expect(JSON.parse(String(init!.body)).knowledge_base_ids).toEqual(['kb-2'])

    await userEvent.click(screen.getByRole('button', { name: 'Open result 1 in context' }))
    expect(await screen.findByRole('dialog', { name: 'Expense Policy.pdf' })).toBeInTheDocument()
  })

  it('shows the topic it searched for when the request was phrased as a sentence', async () => {
    backend(() => json(RESPONSE))
    renderSignedIn('/search?q=Find%20everything%20related%20to%20expense%20approvals')

    expect(await screen.findByTestId('searched-for')).toHaveTextContent('Searched for “expense approvals”')
  })

  it('never invents relevance when the reranker is off', async () => {
    backend(() => json({ ...RESPONSE, reranker: null, results: RESPONSE.results.map((r) => ({ ...r, relevance: null, rerank_score: null })) }))
    renderSignedIn('/search?q=expense')

    expect(await screen.findByTestId('result-count')).toHaveTextContent('3 results')
    expect(screen.getByTestId('relevance-unavailable')).toHaveTextContent("Relevance isn't rated")
    expect(screen.queryByText(/Relevance: /)).not.toBeInTheDocument()
  })

  it('shows empty results and errors', async () => {
    backend(() => json({ ...RESPONSE, results: [] }))
    const { unmount } = renderSignedIn('/search?q=sourdough')
    expect(await screen.findByText(/No passages matched/)).toBeInTheDocument()
    unmount()

    backend(() => errorEnvelope(429, 'rate_limited', 'Too many requests.'))
    renderSignedIn('/search?q=again')
    expect(await screen.findByText('Too many requests.')).toBeInTheDocument()
  })
})
