import { LONG_TIMEOUT_MS, apiRequest } from './api'

export type AnswerType = 'knowledge_base' | 'not_found' | 'general' | 'image' | 'multimodal'

export interface AnswerSource {
  number: number
  chunk_id: string
  document_id: string
  knowledge_base_id: string
  filename: string
  page_number: number | null
  section: string | null
  content: string
  rerank_score: number | null
  similarity: number | null
}

export interface AnswerQuote {
  text: string
  /** Offsets of `text` in the source content; null when the backend could not verify them. */
  start: number | null
  end: number | null
}

export interface AnswerCitation {
  source_number: number
  document_id: string
  filename: string
  page_number: number | null
  section: string | null
  quotes: AnswerQuote[]
  answer_spans: [number, number][]
}

export interface AnswerResponse {
  question: string
  answer: string
  answer_type: AnswerType
  grounded: boolean
  citations: AnswerCitation[]
  sources: AnswerSource[]
  model: string | null
  usage: { input_tokens: number; output_tokens: number } | null
  truncated: boolean
  retrieval: { vector_candidates: number; keyword_candidates: number; filtered_out: number; reranked: boolean } | null
  timings_ms: Record<string, number>
}

export function askQuestion(question: string, knowledgeBaseIds: string[]): Promise<AnswerResponse> {
  return apiRequest<AnswerResponse>('/rag/answer', {
    method: 'POST',
    json: { question, knowledge_base_ids: knowledgeBaseIds },
    timeoutMs: LONG_TIMEOUT_MS,
  })
}
