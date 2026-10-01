import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '../../services/chat'
import { AnswerTrace } from './AnswerTrace'

const ANSWER: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'Employees get 18 days.',
  created_at: '2026-10-01T10:00:00Z',
  answer_type: 'knowledge_base',
  grounded: true,
  knowledge_base_id: 'kb-1',
  retrieval_query: 'annual leave days employees',
  model: 'claude-opus-5',
  usage: { input_tokens: 1200, output_tokens: 40 },
  truncated: false,
  timings_ms: { rewrite: 820, retrieval: 24, rerank: 610, llm: 2300, total: 3760 },
  retrieval: {
    vector_candidates: 20,
    keyword_candidates: 6,
    filtered_out: 3,
    duplicates_removed: 1,
    filter_documents: 2,
    reranked: true,
    reranker: 'rerank-2.5',
    rerank_candidates: 18,
    below_rerank_threshold: 0,
    over_budget: 0,
    context_passages: 5,
    context_chars: 4210,
    citation_check: { cited_sources: 2, quotes: 3, verified_quotes: 3, rejected: 0 },
    rewritten: true,
  },
  citations: [],
  sources: [],
}

async function open(message: ChatMessage) {
  render(<AnswerTrace message={message} />)
  await userEvent.click(screen.getByText('How this answer was found'))
  return screen.getByRole('list')
}

describe('AnswerTrace', () => {
  it('describes reranking honestly for older answers and when the reranker was off', async () => {
    const older = { ...ANSWER.retrieval!, reranker: undefined, rerank_candidates: undefined }
    const { unmount } = render(<AnswerTrace message={{ ...ANSWER, retrieval: older }} />)
    await userEvent.click(screen.getByText('How this answer was found'))
    expect(screen.getByRole('list')).toHaveTextContent('Reordered by the reranker')
    unmount()

    await open({ ...ANSWER, retrieval: { ...ANSWER.retrieval!, reranked: false, reranker: null } })
    expect(screen.getByRole('list')).toHaveTextContent('Kept the retrieval order (reranker off or unavailable)')
  })

  it('walks through every pipeline stage with what it did', async () => {
    const steps = within(await open(ANSWER)).getAllByRole('listitem').map((item) => item.textContent)

    expect(steps).toEqual([
      expect.stringContaining('Rewritten for search: “annual leave days employees”'),
      expect.stringContaining('2 documents matched the filters'),
      expect.stringMatching(/20 semantic \+ 6 keyword candidates, fused; 3 below the similarity threshold, 1 near-duplicate removed/),
      expect.stringContaining('18 passages scored by rerank-2.5'),
      expect.stringContaining('5 passages sent (4,210 characters)'),
      expect.stringContaining('claude-opus-5 · 1,200 in / 40 out tokens'),
      expect.stringContaining('3 of 3 quotes found word for word in the sources'),
    ])
    expect(screen.getByText(/How this answer was found/).parentElement).toHaveTextContent('3.8 s')
  })

  it('flags quotes that could not be verified', async () => {
    const message = {
      ...ANSWER,
      retrieval: { ...ANSWER.retrieval!, citation_check: { cited_sources: 1, quotes: 2, verified_quotes: 1, rejected: 1 } },
    }
    const citations = within(await open(message)).getByText(/1 of 2 quotes/)

    expect(citations).toHaveTextContent('1 citation to unknown sources dropped')
    expect(citations.className).toContain('amber')
  })

  it('handles older messages and general answers', async () => {
    const legacy = { ...ANSWER, retrieval_query: null, retrieval: { vector_candidates: 5, keyword_candidates: 2, filtered_out: 0, reranked: false } }
    const steps = within(await open(legacy)).getAllByRole('listitem')

    expect(steps.map((s) => s.textContent)).toEqual([
      expect.stringContaining('Searched with your question as written'),
      expect.stringContaining('5 semantic + 2 keyword candidates, fused'),
      expect.stringContaining('Kept the retrieval order'),
      expect.stringContaining('claude-opus-5'),
    ])

    const { container } = render(<AnswerTrace message={{ ...ANSWER, retrieval: null }} />)
    expect(container).toBeEmptyDOMElement()
  })
})
