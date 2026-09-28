import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useResource } from '../../hooks/useResource'
import { ApiError } from '../../services/api'
import { getChunkContext, type ContextChunk } from '../../services/chunks'
import { downloadDocument, openPdfInNewTab } from '../../services/documents'
import type { AnswerQuote } from '../../services/rag'
import { highlightRanges, type Range } from '../../utils/highlight'
import { ErrorAlert } from '../ui/Alert'
import { Button } from '../ui/Button'

export interface ViewedSource {
  /** null when the passage no longer exists (document deleted or re-indexed): `snapshot` is shown instead. */
  chunkId: string | null
  documentId: string | null
  filename: string
  pageNumber: number | null
  section: string | null
  /** e.g. "Source 2" for a citation; omitted for search results. */
  label?: string
  quotes?: AnswerQuote[]
  /** The passage text as it was when cited; used when the live passage is gone. */
  snapshot?: string
}

function locationOf(chunk: Pick<ContextChunk, 'page_number' | 'section'>): string | null {
  return chunk.page_number ? `Page ${chunk.page_number}` : chunk.section
}

function Neighbour({ chunk }: { chunk: ContextChunk }) {
  return (
    <p className="text-sm whitespace-pre-line text-slate-500 dark:text-slate-500" data-testid="neighbour-chunk">
      {chunk.content}
    </p>
  )
}

/**
 * Dialog showing a cited passage in context: the chunk with the quoted text highlighted,
 * the chunks around it, and actions to open the original (PDFs at the cited page) or download it.
 */
export function SourceViewer({ source, onClose }: { source: ViewedSource; onClose: () => void }) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const { chunkId } = source
  const context = useResource(
    useCallback(() => (chunkId ? getChunkContext(chunkId, 1) : Promise.resolve(null)), [chunkId]),
  )
  // Fall back to the saved text when the live passage is gone (deleted, re-indexed or 404).
  const liveGone = chunkId === null || context.error?.status === 404
  const snapshot = liveGone && source.snapshot ? source.snapshot : null
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const appRoot = document.getElementById('root')
    appRoot?.setAttribute('inert', '')
    const onKeyDown = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      appRoot?.removeAttribute('inert')
      previouslyFocused?.focus()
    }
  }, [onClose])

  const located: Range[] = (source.quotes ?? [])
    .filter((quote) => quote.start !== null && quote.end !== null)
    .map((quote) => [quote.start!, quote.end!])
  const unlocated = (source.quotes ?? []).filter((quote) => quote.start === null)
  const isPdf = context.data?.document.extension === '.pdf' || source.filename.toLowerCase().endsWith('.pdf')
  const location = source.pageNumber ? `Page ${source.pageNumber}` : source.section

  async function run(action: () => Promise<void>, failure: string) {
    setActionError(null)
    try {
      await action()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : failure)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-stretch sm:justify-end">
      <div aria-hidden className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative flex max-h-[85svh] w-full flex-col rounded-t-2xl bg-white shadow-xl outline-none sm:max-h-none sm:w-[30rem] sm:rounded-none dark:bg-slate-900"
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div className="min-w-0">
            {source.label && <p className="text-xs font-semibold tracking-wide text-brand-600 uppercase dark:text-brand-300">{source.label}</p>}
            <h2 id={titleId} className="truncate font-semibold" title={source.filename}>
              {source.filename}
            </h2>
            {location && <p className="text-sm text-slate-500 dark:text-slate-400">{location}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close source"
            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 dark:text-slate-400"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden>
              <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="flex gap-2 border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          {isPdf && source.documentId && !liveGone && (
            <Button variant="secondary" onClick={() => run(() => openPdfInNewTab(source.documentId!, source.pageNumber), 'Could not open the PDF.')}>
              {source.pageNumber ? `Open page ${source.pageNumber}` : 'Open PDF'}
            </Button>
          )}
          {source.documentId && !liveGone && (
            <Button
              variant="secondary"
              onClick={() => run(() => downloadDocument({ id: source.documentId!, filename: source.filename }), 'Download failed.')}
            >
              Download
            </Button>
          )}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {actionError && <ErrorAlert>{actionError}</ErrorAlert>}
          {snapshot !== null ? (
            <>
              <p className="rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-400" role="note">
                The original document is no longer available. This is the passage as it was when it was cited.
              </p>
              <div className="rounded-lg border-l-4 border-slate-400 bg-slate-50 px-4 py-3 dark:bg-slate-800/50">
                <p className="text-sm leading-relaxed whitespace-pre-line" data-testid="cited-chunk">
                  {highlightRanges(snapshot, located)}
                </p>
              </div>
            </>
          ) : context.error ? (
            <ErrorAlert>
              {context.error.status === 404
                ? 'This passage is no longer available. The document may have been deleted or re-indexed.'
                : context.error.message}
            </ErrorAlert>
          ) : !context.data ? (
            <div className="space-y-2" aria-label="Loading source" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-4 animate-pulse rounded bg-slate-200/70 dark:bg-slate-800" />
              ))}
            </div>
          ) : (
            <>
              {context.data.before.map((chunk) => (
                <Neighbour key={chunk.id} chunk={chunk} />
              ))}
              <div className="rounded-lg border-l-4 border-brand-500 bg-brand-50/50 px-4 py-3 dark:bg-brand-500/10">
                {locationOf(context.data.chunk) && context.data.chunk.page_number !== source.pageNumber && (
                  <p className="mb-1 text-xs text-slate-500 dark:text-slate-400">{locationOf(context.data.chunk)}</p>
                )}
                <p className="text-sm leading-relaxed whitespace-pre-line" data-testid="cited-chunk">
                  {highlightRanges(context.data.chunk.content, located)}
                </p>
              </div>
              {context.data.after.map((chunk) => (
                <Neighbour key={chunk.id} chunk={chunk} />
              ))}
              {unlocated.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Quoted by the answer:</p>
                  {unlocated.map((quote) => (
                    <blockquote key={quote.text} className="mt-1 border-l-2 border-slate-200 pl-3 text-xs text-slate-600 italic dark:border-slate-700 dark:text-slate-400">
                      “{quote.text}”
                    </blockquote>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
