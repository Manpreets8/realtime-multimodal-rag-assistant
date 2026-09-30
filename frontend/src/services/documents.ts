import { apiBlob, apiRequest, uploadForm, type UploadOptions } from './api'

export type DocumentStatus = 'uploaded' | 'processing' | 'completed' | 'failed'

/** How often the UI refreshes documents that are still being ingested. */
export const STATUS_POLL_INTERVAL_MS = 2000

/** Live detail while a worker processes the document (kept in Redis, may be absent). */
export interface IngestionProgress {
  stage: 'extracting' | 'chunking' | 'embedding' | 'indexing'
  done: number
  total: number
  updated_at: number
}

/** Warn about a missing ingestion worker once a document has waited this long. */
export const WORKER_WARNING_AFTER_MS = 20_000

export interface DocumentItem {
  id: string
  knowledge_base_id: string
  filename: string
  extension: string
  content_type: string
  size_bytes: number
  status: DocumentStatus
  error_message: string | null
  /** Why processing failed (e.g. password_protected, no_text, embedding_failed); see DocumentIntelligence. */
  error_code?: string | null
  page_count: number | null
  chunk_count: number
  created_at: string
  processing_started_at: string | null
  processed_at: string | null
  progress?: IngestionProgress | null
  extracted_metadata?: DocumentMetadata | null
  processing_stats?: ProcessingStats | null
}

/** What extraction found; title, author and date come from the file's own properties. */
export interface DocumentMetadata {
  title: string | null
  author: string | null
  document_date: string | null // YYYY-MM-DD
  word_count: number
  character_count: number
  section_count: number
  table_count: number
}

/** Timings of the last successful processing run. */
export interface ProcessingStats {
  extraction_ms: number
  chunking_ms: number
  embedding_ms: number
  indexing_ms: number
  total_ms: number
  embedding_model: string
  characters: number
}

export interface UploadConfig {
  max_file_size: number
  supported_types: { extension: string; content_type: string; label: string }[]
}

export function getUploadConfig(): Promise<UploadConfig> {
  return apiRequest<UploadConfig>('/documents/upload-config')
}

export function listDocuments(knowledgeBaseId: string): Promise<DocumentItem[]> {
  return apiRequest<DocumentItem[]>(`/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`)
}

/** Queue a failed (or completed) document to be ingested again. */
export function reprocessDocument(id: string): Promise<DocumentItem> {
  return apiRequest<DocumentItem>(`/documents/${encodeURIComponent(id)}/reprocess`, { method: 'POST' })
}

export const isPending = (document: Pick<DocumentItem, 'status'>): boolean =>
  document.status === 'uploaded' || document.status === 'processing'

export function deleteDocument(id: string): Promise<null> {
  return apiRequest<null>(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Download with the session token, then hand the file to the browser's save dialog. */
export async function downloadDocument(document: Pick<DocumentItem, 'id' | 'filename'>): Promise<void> {
  const { blob, filename } = await apiBlob(`/documents/${encodeURIComponent(document.id)}/download`)
  const url = URL.createObjectURL(blob)
  try {
    const link = window.document.createElement('a')
    link.href = url
    link.download = filename ?? document.filename
    link.click()
  } finally {
    // Revoke on the next tick so the navigation triggered by click() has started.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

/**
 * Open a PDF in a new tab, at `page` when given. The tab is opened synchronously
 * (still inside the click) so pop-up blockers allow it, then pointed at the file
 * once it has been fetched with the session token.
 */
export async function openPdfInNewTab(documentId: string, page?: number | null): Promise<void> {
  const tab = window.open('', '_blank')
  try {
    const { blob } = await apiBlob(`/documents/${encodeURIComponent(documentId)}/download?inline=true`)
    const url = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
    if (tab) {
      tab.opener = null
      tab.location.href = page ? `${url}#page=${page}` : url
    }
    // Keep the URL alive long enough for the viewer to load it.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    tab?.close()
    throw error
  }
}

export type { UploadOptions }

/**
 * Upload one file. Uses XMLHttpRequest because fetch() cannot report upload
 * progress; errors are normalised exactly like `apiRequest`.
 */
export function uploadDocument(knowledgeBaseId: string, file: File, options: UploadOptions = {}): Promise<DocumentItem> {
  const form = new FormData()
  form.append('knowledge_base_id', knowledgeBaseId)
  form.append('file', file)
  return uploadForm<DocumentItem>('/documents/upload', form, options)
}

export interface DocumentListItem extends DocumentItem {
  knowledge_base_name: string
}

export interface DocumentPage {
  items: DocumentListItem[]
  total: number
}

/** All of the user's documents across knowledge bases. */
export function listAllDocuments(params: {
  status?: DocumentStatus
  search?: string
  limit: number
  offset: number
}): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) })
  if (params.status) query.set('status', params.status)
  if (params.search) query.set('search', params.search)
  return apiRequest<DocumentPage>(`/documents?${query}`)
}
