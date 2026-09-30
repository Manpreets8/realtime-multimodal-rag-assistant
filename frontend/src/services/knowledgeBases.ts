import { apiRequest } from './api'
import type { DocumentStatus } from './documents'

export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  document_count: number
  status_counts: Partial<Record<DocumentStatus, number>>
  passage_count: number
  total_bytes: number
  /** Conversations answering from it; they become general chats if it is deleted. */
  conversation_count: number
  created_at: string
  updated_at: string
  /** Latest change to it or any of its documents. */
  last_activity_at: string | null
}

export type KnowledgeBaseSort = 'recent' | 'name' | 'created'

export interface KnowledgeBaseInput {
  name: string
  description: string | null
}

export function listKnowledgeBases(options: { search?: string; sort?: KnowledgeBaseSort } = {}): Promise<KnowledgeBase[]> {
  const query = new URLSearchParams()
  if (options.search) query.set('search', options.search)
  if (options.sort && options.sort !== 'recent') query.set('sort', options.sort)
  const suffix = query.size ? `?${query}` : ''
  return apiRequest<KnowledgeBase[]>(`/knowledge-bases${suffix}`)
}

export function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>(`/knowledge-bases/${encodeURIComponent(id)}`)
}

export function createKnowledgeBase(input: KnowledgeBaseInput): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>('/knowledge-bases', { method: 'POST', json: input })
}

export function updateKnowledgeBase(id: string, input: Partial<KnowledgeBaseInput>): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>(`/knowledge-bases/${encodeURIComponent(id)}`, { method: 'PATCH', json: input })
}

export function deleteKnowledgeBase(id: string): Promise<null> {
  return apiRequest<null>(`/knowledge-bases/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
