import { apiRequest } from './api'

export type SearchMode = 'hybrid' | 'vector' | 'keyword'

export interface SearchHit {
  chunk_id: string
  document_id: string
  knowledge_base_id: string
  filename: string
  chunk_index: number
  page_number: number | null
  section: string | null
  content: string
  score: number
  similarity: number | null
  keyword_score: number | null
  vector_rank: number | null
  keyword_rank: number | null
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  results: SearchHit[]
  vector_candidates: number
  keyword_candidates: number
  filtered_out: number
  similarity_threshold: number
  timings_ms: Record<string, number>
}

export interface SearchRequest {
  query: string
  knowledge_base_ids: string[]
  mode?: SearchMode
  limit?: number
}

export function search(request: SearchRequest): Promise<SearchResponse> {
  return apiRequest<SearchResponse>('/retrieval/search', { method: 'POST', json: request })
}
