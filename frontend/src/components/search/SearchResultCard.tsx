import type { ReactNode } from 'react'

import type { Relevance, SearchHit } from '../../services/retrieval'

// Common English words not worth highlighting (they match almost every passage).
const STOP_WORDS = new Set(
  'the and for are but not you all any can had her was one our out has have how its may per who why what when where which will with does this that from they them their there about into than then also just only much many get'.split(' '),
)

/** Wrap words in `text` that start with a meaningful query word (3+ letters) in <mark>, without
 * injecting HTML. Matching at word starts highlights "Hotels" for "hotel" (the search stems words
 * too) but never fragments inside other words, like "get" in "budget". */
function highlight(text: string, query: string): ReactNode[] {
  const words = [...new Set(query.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? [])].filter((word) => !STOP_WORDS.has(word))
  if (words.length === 0) return [text]
  const escaped = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`((?<![\\p{L}\\p{N}])(?:${escaped.join('|')})[\\p{L}\\p{N}]*)`, 'giu')
  return text.split(pattern).map((part, index) =>
    index % 2 === 1 ? (
      <mark key={index} className="rounded bg-amber-100 px-0.5 text-inherit dark:bg-amber-500/25">
        {part}
      </mark>
    ) : (
      part
    ),
  )
}

function location(hit: SearchHit): string {
  const parts = [hit.page_number ? `Page ${hit.page_number}` : null, hit.section].filter(Boolean)
  return parts.length ? parts.join(' · ') : `Chunk ${hit.chunk_index + 1}`
}

const RELEVANCE: Record<Relevance, { label: string; className: string }> = {
  high: { label: 'High', className: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300' },
  medium: { label: 'Medium', className: 'bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300' },
  low: { label: 'Low', className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300' },
}

export interface SearchResultCardProps {
  hit: SearchHit
  rank: number
  query: string
  /** Model that produced `hit.rerank_score` (null when the results were not reranked). */
  reranker: string | null
  onView: () => void
  /** Shown under the file name, e.g. when results come from several knowledge bases. */
  knowledgeBaseName?: string
  /** "detailed" shows every retriever score (Search tab); "simple" shows the relevance label. */
  variant?: 'detailed' | 'simple'
}

/** One search result: file, location, scores or relevance, the passage with query words highlighted. */
export function SearchResultCard({
  hit,
  rank,
  query,
  reranker,
  onView,
  knowledgeBaseName,
  variant = 'detailed',
}: SearchResultCardProps) {
  const relevance = hit.relevance ? RELEVANCE[hit.relevance] : null
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            <span className="mr-2 text-slate-500 dark:text-slate-400">#{rank}</span>
            {hit.filename}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {location(hit)}
            {knowledgeBaseName && ` · ${knowledgeBaseName}`}
          </p>
        </div>
        {variant === 'simple' ? (
          relevance && (
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${relevance.className}`}
              title={`From the ${reranker ?? 'reranker'} score ${hit.rerank_score?.toFixed(2)}; bands measured on the evaluation set`}
            >
              Relevance: {relevance.label}
            </span>
          )
        ) : (
        <div className="flex flex-wrap gap-1.5 text-xs" aria-label="Scores">
          {reranker && hit.rerank_score !== null && hit.rerank_score !== undefined && (
            <span
              className="rounded-full bg-violet-50 px-2 py-0.5 font-medium text-violet-800 dark:bg-violet-500/15 dark:text-violet-200"
              title={`Relevance score from ${reranker} (model-specific scale)`}
            >
              Rerank {hit.rerank_score.toFixed(2)}
            </span>
          )}
          {hit.similarity !== null && (
            <span className="rounded-full bg-brand-50 px-2 py-0.5 font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-100" title="Cosine similarity">
              Similarity {hit.similarity.toFixed(2)}
            </span>
          )}
          {hit.keyword_rank !== null && (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300" title="Matched the keyword search">
              Keyword #{hit.keyword_rank}
            </span>
          )}
        </div>
        )}
      </div>
      <p className="mt-3 line-clamp-6 text-sm whitespace-pre-line text-slate-700 dark:text-slate-300">
        {highlight(hit.content, query)}
      </p>
      <button
        type="button"
        onClick={onView}
        className="mt-2 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
        aria-label={`${variant === 'simple' ? 'Open' : 'View'} result ${rank} in context`}
      >
        {variant === 'simple' ? 'Open source' : 'View in context'}
      </button>
    </li>
  )
}
