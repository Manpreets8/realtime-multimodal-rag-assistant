/** What a document's processing failure code means for the user: advice, and whether Retry can
 * work without changing the file. Codes are set by the backend (app/services/ingestion_service.py).
 * Advice is only given where the backend's own message doesn't already say what to do. */
import type { DocumentItem } from '../services/documents'

const FAILURES: Record<string, { advice?: string; retryable: boolean }> = {
  password_protected: { retryable: false }, // "…Remove the password and upload it again."
  file_missing: { retryable: false }, // "…Please upload it again."
  damaged_file: { advice: 'Open and save the file again (or export it again), then upload it.', retryable: false },
  no_text: { advice: 'Upload a version with selectable text, or run OCR on the scan first.', retryable: false },
  too_many_pages: { advice: 'Split the PDF into parts, then upload them.', retryable: false },
  too_large: { advice: 'Save a smaller copy of the Word file and upload it.', retryable: false },
  unsupported_type: { advice: 'Upload a PDF, Word, text or Markdown file.', retryable: false },
  embedding_failed: { advice: 'This is usually temporary. Retrying usually works.', retryable: true },
  internal_error: { advice: 'Retry; if it keeps failing, report it with the document name.', retryable: true },
}

/** Whether retrying can succeed without changing the file. Unknown or missing codes (documents
 * processed before codes were recorded) stay retryable. */
export function canRetry(document: DocumentItem): boolean {
  return FAILURES[document.error_code ?? '']?.retryable ?? true
}

export function failureAdvice(document: DocumentItem): string | null {
  return FAILURES[document.error_code ?? '']?.advice ?? null
}
