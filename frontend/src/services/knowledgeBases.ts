import { apiRequest } from './api'
import type { DocumentStatus } from './documents'

export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  document_count: number
  status_counts: Partial<Record<DocumentStatus, number>>
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseInput {
  name: string
  description: string | null
}

export function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return apiRequest<KnowledgeBase[]>('/knowledge-bases')
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
