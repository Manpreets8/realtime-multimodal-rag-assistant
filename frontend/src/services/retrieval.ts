import { apiRequest } from './api'

export type SearchMode = 'hybrid' | 'vector' | 'keyword'
export type FusionMethod = 'rrf' | 'weighted'
export type Relevance = 'high' | 'medium' | 'low'

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
  /** Plain-language band of the rerank score; only for rerankers with measured bands. */
  relevance?: Relevance | null
  /** The reranker's own score; only present when reranking was requested and succeeded. */
  rerank_score?: number | null
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  results: SearchHit[]
  vector_candidates: number
  keyword_candidates: number
  filtered_out: number
  duplicates_removed?: number
  /** Documents matching the filters (null when no filters were sent). */
  filter_documents?: number | null
  similarity_threshold: number
  /** The parameters the search actually used (server settings plus any overrides). */
  parameters?: SearchParameters | null
  /** Model that reranked the results (null: not requested, reranker off, or it failed). */
  reranker?: string | null
  timings_ms: Record<string, number>
}

export interface SearchRequest {
  query: string
  /** Empty searches all of the user's knowledge bases. */
  knowledge_base_ids: string[]
  mode?: SearchMode
  limit?: number
  filters?: SearchFilters
  options?: SearchOptions
}

/** Per-search overrides of the server's retrieval settings; omitted fields use the server's. */
export interface SearchOptions {
  candidates?: number // per retriever, before fusion (1-100)
  similarity_threshold?: number // 0-1
  fusion?: FusionMethod
  alpha?: number // weighted fusion: weight of semantic similarity, 0-1
  rerank?: boolean // re-score with the server's reranker, as chat answers do
  topic?: boolean // drop request phrasing ("Find everything related to") before searching
}

export interface SearchParameters {
  candidates: number
  limit: number
  similarity_threshold: number
  fusion: FusionMethod
  alpha: number
  dedup_threshold: number | null
  rerank?: boolean
}

/** Restrict retrieval to some documents; empty fields don't filter. */
export interface SearchFilters {
  document_ids?: string[]
  file_types?: string[] // extensions, e.g. ".pdf"
  uploaded_after?: string
  uploaded_before?: string
}

export function search(request: SearchRequest): Promise<SearchResponse> {
  return apiRequest<SearchResponse>('/retrieval/search', { method: 'POST', json: request })
}
