import { apiRequest } from './api'
import type { DocumentItem } from './documents'

export interface ContextChunk {
  id: string
  chunk_index: number
  page_number: number | null
  section: string | null
  content: string
}

export interface ChunkContext {
  chunk: ContextChunk
  before: ContextChunk[]
  after: ContextChunk[]
  document: DocumentItem
}

export function getChunkContext(chunkId: string, neighbors = 1): Promise<ChunkContext> {
  return apiRequest<ChunkContext>(`/chunks/${encodeURIComponent(chunkId)}?neighbors=${neighbors}`)
}
