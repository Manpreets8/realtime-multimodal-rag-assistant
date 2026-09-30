import { apiRequest } from './api'

export interface DailyCount {
  date: string // UTC day, YYYY-MM-DD
  count: number
}

export interface DashboardStats {
  knowledge_bases: number
  documents: { total: number; indexed: number; processing: number; failed: number }
  conversations: number
  ai_answers: { days: number; answers: number; input_tokens: number; output_tokens: number; by_day: DailyCount[] }
}

export type ActivityKind =
  | 'knowledge_base_created'
  | 'document_uploaded'
  | 'document_ready'
  | 'document_failed'
  | 'conversation_started'

export interface ActivityItem {
  kind: ActivityKind
  at: string
  title: string
  detail: string | null
  knowledge_base_id: string | null
  conversation_id: string | null
}

export interface Dashboard {
  stats: DashboardStats
  activity: ActivityItem[]
}

export function getDashboard(): Promise<Dashboard> {
  return apiRequest<Dashboard>('/dashboard')
}

export interface ProviderStatus {
  capability: 'llm' | 'vision' | 'embeddings' | 'reranking' | 'speech_to_text' | 'text_to_speech'
  provider: string
  model: string
  runs: 'local' | 'api' | 'off'
  configured: boolean
}

export function getProviders(): Promise<{ app: string; tagline: string; providers: ProviderStatus[] }> {
  return apiRequest('/system/providers')
}
