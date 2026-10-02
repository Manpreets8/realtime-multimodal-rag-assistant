import { apiRequest } from './api'
import type { AnswerCitation } from './rag'

export interface ComparedDocument {
  id: string
  knowledge_base_id: string
  filename: string
  page_count: number | null
  sentences: number
  /** Share of the text given to the AI model (1 = all); null without an analysis. */
  coverage: number | null
}

/** A sentence exactly as it appears in a document, with where it is. */
export interface TextUnit {
  text: string
  chunk_id: string
  page_number: number | null
  section: string | null
}

export interface ModifiedUnit {
  before: TextUnit
  after: TextUnit
  similarity: number
}

export interface TextDifferences {
  counts: { added: number; removed: number; modified: number; common: number }
  overlap: number
  added: TextUnit[]
  removed: TextUnit[]
  modified: ModifiedUnit[]
  common: TextUnit[]
  listed_limit: number
}

export interface ComparisonSource {
  number: number
  chunk_id: string
  document_id: string
  knowledge_base_id: string
  filename: string
  page_number: number | null
  section: string | null
  content: string
}

export interface ComparisonAnalysis {
  text: string
  citations: AnswerCitation[]
  sources: ComparisonSource[]
  cited_documents: string[]
  citation_check: { cited_sources: number; quotes: number; verified_quotes: number; rejected: number }
  model: string
  usage: { input_tokens: number; output_tokens: number }
  truncated: boolean
}

export interface CompareResponse {
  document_a: ComparedDocument
  document_b: ComparedDocument
  differences: TextDifferences
  analysis: ComparisonAnalysis | null
  analysis_unavailable: string | null
  timings_ms: Record<string, number>
}

export function compareDocuments(documentAId: string, documentBId: string): Promise<CompareResponse> {
  return apiRequest<CompareResponse>('/documents/compare', {
    method: 'POST',
    json: { document_a_id: documentAId, document_b_id: documentBId },
  })
}
