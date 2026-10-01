import { useCallback, useId, useState } from 'react'

import { usePolling } from '../../hooks/usePolling'
import { useResource } from '../../hooks/useResource'
import { ApiError } from '../../services/api'
import type { DocumentItem } from '../../services/documents'
import { getInsights, requestInsights, type EntityType, type InsightContent } from '../../services/insights'
import { formatRelative, pluralize } from '../../utils/format'
import { ErrorAlert } from '../ui/Alert'
import { Button } from '../ui/Button'
import { Skeleton } from '../ui/Skeleton'

const POLL_MS = 2500
const WORDS_PER_MINUTE = 230

type SummaryKind = 'short' | 'detailed' | 'technical'
const SUMMARIES: { id: SummaryKind; label: string }[] = [
  { id: 'short', label: 'Short' },
  { id: 'detailed', label: 'Detailed' },
  { id: 'technical', label: 'Technical' },
]

const ENTITY_LABELS: Record<EntityType, string> = {
  person: 'People',
  organization: 'Organizations',
  location: 'Locations',
  product: 'Products',
  technology: 'Technologies',
  date: 'Dates',
  other: 'Other',
}

const HEADING = 'text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400'
const CHIP = 'rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300'

function Summaries({ content }: { content: InsightContent }) {
  const [kind, setKind] = useState<SummaryKind>('short')
  const id = useId()
  const text = { short: content.short_summary, detailed: content.detailed_summary, technical: content.technical_summary }[kind]
  return (
    <section aria-label="Summary">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className={HEADING}>Summary</h4>
        <div role="tablist" aria-label="Summary type" className="flex gap-1">
          {SUMMARIES.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              id={`${id}-${option.id}`}
              aria-selected={kind === option.id}
              aria-controls={`${id}-panel`}
              onClick={() => setKind(option.id)}
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                kind === option.id
                  ? 'bg-brand-600 text-white'
                  : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${kind}`} className="mt-2 space-y-2 text-sm leading-relaxed">
        {text.split(/\n{2,}/).map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
    </section>
  )
}

function Chips({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <section aria-label={title}>
      <h4 className={HEADING}>{title}</h4>
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {items.map((item) => (
          <li key={item} className={CHIP}>
            {item}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Entities({ entities }: { entities: InsightContent['entities'] }) {
  if (entities.length === 0) return null
  const groups = (Object.keys(ENTITY_LABELS) as EntityType[])
    .map((type) => [type, entities.filter((entity) => entity.type === type).map((entity) => entity.name)] as const)
    .filter(([, names]) => names.length > 0)
  return (
    <section aria-label="Entities">
      <h4 className={HEADING}>Entities</h4>
      <dl className="mt-2 space-y-1.5 text-sm">
        {groups.map(([type, names]) => (
          <div key={type} className="flex flex-wrap gap-x-2">
            <dt className="text-slate-500 dark:text-slate-400">{ENTITY_LABELS[type]}:</dt>
            <dd>{names.join(', ')}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function Statistics({ document }: { document: DocumentItem }) {
  const words = document.extracted_metadata?.word_count
  const items: [string, string][] = [
    ['Pages', document.page_count ? String(document.page_count) : '—'],
    ['Words', words !== undefined ? words.toLocaleString('en') : '—'],
    ['Reading time', words ? `${Math.max(1, Math.round(words / WORDS_PER_MINUTE))} min` : '—'],
    ['Sections', document.extracted_metadata ? String(document.extracted_metadata.section_count) : '—'],
    ['Passages', String(document.chunk_count)],
  ]
  return (
    <section aria-label="Document statistics">
      <h4 className={HEADING}>Statistics</h4>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
        {items.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

/** AI-generated summaries, key points, topics, keywords and entities for a processed document.
 * Generation is on request (it uses the AI model) and runs in the background. */
export function InsightsPanel({ document }: { document: DocumentItem }) {
  const fetchInsights = useCallback(() => getInsights(document.id), [document.id])
  const { data, error, setData } = useResource(fetchInsights)
  const [requesting, setRequesting] = useState(false)
  const [requestError, setRequestError] = useState<ApiError | null>(null)

  usePolling(async () => setData(await getInsights(document.id)), data?.status === 'pending', POLL_MS)

  async function generate() {
    setRequesting(true)
    setRequestError(null)
    try {
      setData(await requestInsights(document.id))
    } catch (err) {
      setRequestError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'The request failed.', null))
    } finally {
      setRequesting(false)
    }
  }

  const content = data?.content ?? null
  const pending = data?.status === 'pending'

  return (
    <section aria-label="AI insights" aria-busy={pending} className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className={HEADING}>AI insights</h3>
        {data && data.status !== 'none' && !pending && (
          <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => void generate()} loading={requesting}>
            Regenerate
          </Button>
        )}
      </div>

      {error && <ErrorAlert>{error.message}</ErrorAlert>}
      {requestError && <ErrorAlert>{requestError.message}</ErrorAlert>}

      {data?.status === 'none' && (
        <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm dark:border-slate-700">
          <p className="text-slate-600 dark:text-slate-400">
            Summaries, key points, topics, keywords and named entities, generated by the AI model from this document. It
            takes a few seconds and uses AI tokens.
          </p>
          <Button className="mt-3" onClick={() => void generate()} loading={requesting}>
            Generate insights
          </Button>
        </div>
      )}

      {pending && (
        <p role="status" className="text-sm text-slate-600 dark:text-slate-400">
          {content ? 'Regenerating insights… the current ones are shown until the new ones are ready.' : 'Generating insights…'}
        </p>
      )}
      {pending && !content && (
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      )}
      {data?.status === 'failed' && data.error_message && <ErrorAlert>{data.error_message}</ErrorAlert>}

      {content && (
        <>
          <Summaries content={content} />
          {content.key_points.length > 0 && (
            <section aria-label="Key points">
              <h4 className={HEADING}>Key points</h4>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {content.key_points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </section>
          )}
          <div className="grid gap-5 md:grid-cols-2">
            <Chips title="Topics" items={content.topics} />
            <Chips title="Keywords" items={content.keywords} />
          </div>
          <Entities entities={content.entities} />
          <Statistics document={document} />
          <p className="text-xs text-slate-500 dark:text-slate-400">
            AI-generated{data?.model ? ` by ${data.model}` : ''}
            {data?.coverage !== null && data?.coverage !== undefined && data.coverage < 1
              ? ` from ${Math.round(data.coverage * 100)}% of the document (an even sample: it is too long to read in full)`
              : ' from the whole document'}
            {data?.generated_at ? `, ${formatRelative(data.generated_at)}` : ''}
            {content.language ? ` · ${content.language}` : ''}
            {data ? ` · ${pluralize(data.llm_calls, 'AI call')} · ${data.input_tokens.toLocaleString('en')} input / ${data.output_tokens.toLocaleString('en')} output tokens` : ''}
            {content.ungrounded_removed > 0 && ` · ${pluralize(content.ungrounded_removed, 'term')} not found in the document removed`}
            . It can contain mistakes: check important details in the document.
          </p>
        </>
      )}
    </section>
  )
}
