import type { ChatMessage } from '../../services/chat'
import { pluralize } from '../../utils/format'

function ms(value: number | undefined): string | null {
  if (value === undefined) return null
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`
}

interface Step {
  label: string
  detail: string
  time?: string | null
  warning?: boolean
}

/** "How this answer was found": each RAG pipeline stage with what it did, from the stats stored
 * with the message. Only for answers that searched a knowledge base. */
export function AnswerTrace({ message }: { message: ChatMessage }) {
  const stats = message.retrieval
  if (!stats) return null
  const timings = message.timings_ms ?? {}
  const check = stats.citation_check
  const steps: Step[] = []

  steps.push({
    label: 'Query',
    detail: message.retrieval_query
      ? `Rewritten for search: “${message.retrieval_query}”`
      : 'Searched with your question as written',
    time: ms(timings.rewrite),
  })
  if (stats.filter_documents !== undefined && stats.filter_documents !== null) {
    steps.push({ label: 'Filters', detail: `${pluralize(stats.filter_documents, 'document')} matched the filters` })
  }
  const dropped = [
    stats.filtered_out ? `${stats.filtered_out} below the similarity threshold` : null,
    stats.duplicates_removed ? `${pluralize(stats.duplicates_removed, 'near-duplicate')} removed` : null,
  ].filter(Boolean)
  steps.push({
    label: 'Retrieval',
    detail: `${stats.vector_candidates} semantic + ${stats.keyword_candidates} keyword candidates, fused${
      dropped.length ? `; ${dropped.join(', ')}` : ''
    }`,
    time: ms(timings.retrieval),
  })
  steps.push({
    label: 'Reranking',
    detail: !stats.reranked
      ? 'Kept the retrieval order (reranker off or unavailable)'
      : stats.reranker
        ? `${stats.rerank_candidates ? pluralize(stats.rerank_candidates, 'passage') : 'Passages'} scored by ${stats.reranker}`
        : 'Reordered by the reranker',
    time: ms(timings.rerank),
  })
  if (stats.context_passages !== undefined) {
    const excluded = [
      stats.below_rerank_threshold ? `${stats.below_rerank_threshold} below the relevance threshold` : null,
      stats.over_budget ? `${stats.over_budget} over the size budget` : null,
    ].filter(Boolean)
    steps.push({
      label: 'Context',
      detail: `${pluralize(stats.context_passages, 'passage')} sent (${(stats.context_chars ?? 0).toLocaleString('en')} characters)${
        excluded.length ? `; ${excluded.join(', ')}` : ''
      }`,
    })
  }
  if (message.model) {
    steps.push({
      label: 'Answer',
      detail: `${message.model}${message.usage ? ` · ${message.usage.input_tokens.toLocaleString('en')} in / ${message.usage.output_tokens.toLocaleString('en')} out tokens` : ''}`,
      time: ms(timings.llm),
    })
  }
  if (check) {
    const allVerified = check.quotes === check.verified_quotes && check.rejected === 0
    steps.push({
      label: 'Citations',
      detail:
        check.quotes === 0
          ? 'No passages quoted'
          : `${check.verified_quotes} of ${pluralize(check.quotes, 'quote')} found word for word in the sources${
              check.rejected ? `; ${pluralize(check.rejected, 'citation')} to unknown sources dropped` : ''
            }`,
      warning: !allVerified,
    })
  }

  return (
    <details className="group mt-3 rounded-lg border border-slate-200 text-xs dark:border-slate-800">
      <summary className="cursor-pointer list-none px-3 py-2 font-medium text-slate-600 select-none hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/60">
        <span aria-hidden className="mr-1 inline-block transition-transform group-open:rotate-90">›</span>
        How this answer was found
        {timings.total !== undefined && <span className="text-slate-500 dark:text-slate-400"> · {ms(timings.total)}</span>}
      </summary>
      <ol className="space-y-2 border-t border-slate-200 px-3 py-3 dark:border-slate-800">
        {steps.map((step, index) => (
          <li key={step.label} className="flex gap-3">
            <span
              aria-hidden
              className={`flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold ${
                step.warning
                  ? 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
              }`}
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-slate-700 dark:text-slate-200">
                {step.label}
                {step.time && <span className="font-normal text-slate-500 dark:text-slate-400"> · {step.time}</span>}
              </p>
              <p className={step.warning ? 'text-amber-800 dark:text-amber-300' : 'text-slate-600 dark:text-slate-400'}>
                {step.detail}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </details>
  )
}
