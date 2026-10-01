import { apiRequest } from './api'

export type EntityType = 'person' | 'organization' | 'location' | 'product' | 'technology' | 'date' | 'other'

export interface InsightContent {
  /** The document's language, as reported by the model (e.g. "English"). */
  language?: string | null
  short_summary: string
  detailed_summary: string
  technical_summary: string
  key_points: string[]
  topics: string[]
  keywords: string[]
  entities: { name: string; type: EntityType }[]
  /** Keywords and entities the model returned that don't occur in the document (removed). */
  ungrounded_removed: number
}

export interface DocumentInsights {
  status: 'none' | 'pending' | 'ready' | 'failed'
  /** Kept while a regeneration is pending or after it failed. */
  content: InsightContent | null
  model: string | null
  input_tokens: number
  output_tokens: number
  llm_calls: number
  /** Share of the document the model read (below 1 only for very long documents). */
  coverage: number | null
  error_message: string | null
  requested_at: string | null
  generated_at: string | null
}

export function getInsights(documentId: string): Promise<DocumentInsights> {
  return apiRequest<DocumentInsights>(`/documents/${encodeURIComponent(documentId)}/insights`)
}

/** Generate or regenerate in the background (uses the AI model); returns `pending`. */
export function requestInsights(documentId: string): Promise<DocumentInsights> {
  return apiRequest<DocumentInsights>(`/documents/${encodeURIComponent(documentId)}/insights`, { method: 'POST' })
}
