import { useCallback, useState } from 'react'

import type { AnswerCitation, AnswerType } from '../../services/rag'
import { SpeakControls } from '../chat/SpeakControls'
import { CitedAnswer } from './CitedAnswer'
import { SourceViewer, type ViewedSource } from './SourceViewer'

/** A source as returned by /rag/answer or stored with a chat message (ids null once the document is gone). */
export interface AnswerBodySource {
  number: number
  chunk_id: string | null
  document_id: string | null
  filename: string
  page_number: number | null
  section: string | null
  content: string
}

export interface AnswerBodyProps {
  text: string
  answerType: AnswerType | string | null
  citations: (Omit<AnswerCitation, 'document_id'> & { document_id: string | null })[]
  sources: AnswerBodySource[]
  truncated?: boolean
  model?: string | null
  usage?: { input_tokens: number; output_tokens: number } | null
  reranked?: boolean
  timingsMs?: Record<string, number> | null
}

const ANSWER_TYPES: Record<string, { label: string; className: string }> = {
  knowledge_base: {
    label: 'Answered from your documents',
    className: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300',
  },
  not_found: {
    label: 'Not found in the knowledge base',
    className: 'bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300',
  },
  general: {
    label: 'General answer (no documents)',
    className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  },
  image: {
    label: 'Answered from your image',
    className: 'bg-violet-50 text-violet-800 dark:bg-violet-500/10 dark:text-violet-300',
  },
  multimodal: {
    label: 'Answered from your image and documents',
    className: 'bg-sky-50 text-sky-800 dark:bg-sky-500/10 dark:text-sky-300',
  },
}

const TIMING_LABELS: Record<string, string> = { rewrite: 'Rewrite', retrieval: 'Retrieval', rerank: 'Rerank', llm: 'LLM', total: 'Total' }

function locationOf(source: { page_number: number | null; section: string | null }): string | null {
  return source.page_number ? `Page ${source.page_number}` : source.section
}

function describeSource(source: AnswerBodySource | undefined): string {
  if (!source) return 'Unknown source'
  const location = locationOf(source)
  return location ? `${source.filename}, ${location}` : source.filename
}

/** Answer text with inline citations, the cited-source list, the context given to the model and diagnostics. */
export function AnswerBody(props: AnswerBodyProps) {
  const { text, answerType, citations, sources, truncated, model, usage, reranked, timingsMs } = props
  const [viewing, setViewing] = useState<ViewedSource | null>(null)
  const close = useCallback(() => setViewing(null), [])
  const type = answerType ? ANSWER_TYPES[answerType] : undefined
  const sourceByNumber = new Map(sources.map((source) => [source.number, source]))
  const citationByNumber = new Map(citations.map((citation) => [citation.source_number, citation]))

  const open = (number: number) => {
    const source = sourceByNumber.get(number)
    if (!source) return
    setViewing({
      chunkId: source.chunk_id,
      documentId: source.document_id,
      filename: source.filename,
      pageNumber: source.page_number,
      section: source.section,
      label: `Source ${number}`,
      quotes: citationByNumber.get(number)?.quotes ?? [],
      snapshot: source.content,
    })
  }

  const diagnostics = [
    model && `Model ${model}`,
    usage && `${usage.input_tokens} in / ${usage.output_tokens} out tokens`,
    reranked && 'reranked',
    ...Object.entries(TIMING_LABELS)
      .filter(([name]) => timingsMs && name in timingsMs)
      .map(([name, label]) => `${label} ${Math.round(timingsMs![name])} ms`),
  ].filter(Boolean)

  return (
    <div className="space-y-3">
      {(type || text.trim()) && (
        <div className="flex flex-wrap items-center gap-2">
          {type && <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${type.className}`}>{type.label}</span>}
          {text.trim() && <SpeakControls key={text} text={text} />}
        </div>
      )}
      <CitedAnswer text={text} citations={citations} describe={(n) => describeSource(sourceByNumber.get(n))} onSelect={open} />
      {truncated && <p className="text-xs text-amber-700 dark:text-amber-400">The answer was cut off because it reached the length limit.</p>}

      {citations.length > 0 && (
        <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">Sources</h3>
          <ol className="mt-2 space-y-1" aria-label="Cited sources">
            {citations.map((citation) => (
              <li key={citation.source_number}>
                <button
                  type="button"
                  onClick={() => open(citation.source_number)}
                  className="group flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded bg-brand-50 text-xs font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-100">
                    {citation.source_number}
                  </span>
                  <span className="min-w-0">
                    <span className="font-medium group-hover:underline">{citation.filename}</span>
                    {locationOf(citation) && <span className="text-slate-500 dark:text-slate-400"> — {locationOf(citation)}</span>}
                    {citation.quotes[0] && (
                      <span className="mt-0.5 block truncate text-xs text-slate-500 italic dark:text-slate-400">“{citation.quotes[0].text}”</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}

      {sources.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 dark:text-slate-400">
            Context given to the model ({sources.length} passages)
          </summary>
          <ol className="mt-2 space-y-3">
            {sources.map((source) => (
              <li key={source.number} className={citationByNumber.has(source.number) ? '' : 'opacity-70'}>
                <p className="flex items-center gap-2 text-xs font-medium">
                  <span>
                    [{source.number}] {source.filename}
                    {locationOf(source) && <span className="text-slate-500 dark:text-slate-400"> — {locationOf(source)}</span>}
                    {!citationByNumber.has(source.number) && <span className="ml-1 text-slate-500 dark:text-slate-400">(not cited)</span>}
                  </span>
                  <button type="button" onClick={() => open(source.number)} className="text-brand-600 hover:underline dark:text-brand-300" aria-label={`View source ${source.number} in context`}>
                    View
                  </button>
                </p>
                <p className="mt-1 line-clamp-3 text-xs whitespace-pre-line text-slate-600 dark:text-slate-400">{source.content}</p>
              </li>
            ))}
          </ol>
        </details>
      )}

      {diagnostics.length > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="answer-diagnostics">
          {diagnostics.join(' · ')}
        </p>
      )}

      {viewing && <SourceViewer source={viewing} onClose={close} />}
    </div>
  )
}
