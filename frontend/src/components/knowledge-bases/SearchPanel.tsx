import { useCallback, useState, type FormEvent, type ReactNode } from 'react'

import { ApiError } from '../../services/api'
import {
  search,
  type FusionMethod,
  type SearchHit,
  type SearchMode,
  type SearchOptions,
  type SearchResponse,
} from '../../services/retrieval'
import { SourceViewer, type ViewedSource } from '../citations/SourceViewer'
import { ErrorAlert } from '../ui/Alert'
import { Button } from '../ui/Button'

const MODES: { value: SearchMode; label: string; hint: string }[] = [
  { value: 'hybrid', label: 'Hybrid', hint: 'Meaning + exact words, merged into one ranking' },
  { value: 'vector', label: 'Semantic', hint: 'Embedding similarity only' },
  { value: 'keyword', label: 'Keyword', hint: 'Postgres full-text search only' },
]

const FILE_TYPES: { value: string; label: string }[] = [
  { value: '.pdf', label: 'PDF' },
  { value: '.docx', label: 'Word' },
  { value: '.txt', label: 'Text' },
  { value: '.md', label: 'Markdown' },
]

const TIMING_LABELS: Record<string, string> = {
  embedding: 'Embed query',
  vector_search: 'Vector',
  keyword_search: 'Keyword',
  fusion: 'Fusion',
  rerank: 'Rerank',
  fetch: 'Fetch',
  total: 'Total',
}

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

const FUSION_LABELS: Record<FusionMethod, string> = { rrf: 'Reciprocal rank fusion', weighted: 'Weighted scores' }

const inputClass =
  'block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm shadow-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900'

/** Advanced settings as typed. Empty fields mean "use the server setting". */
interface AdvancedSettings {
  candidates: string
  threshold: string
  fusion: '' | FusionMethod
  alpha: number
  rerank: boolean
}

const NO_OVERRIDES: AdvancedSettings = { candidates: '', threshold: '', fusion: '', alpha: 0.5, rerank: false }

/** Overrides to send (only those that apply to the mode), or undefined when none are set. */
function toSearchOptions(settings: AdvancedSettings, mode: SearchMode): SearchOptions | undefined {
  const options: SearchOptions = {}
  if (settings.candidates.trim()) options.candidates = Number(settings.candidates)
  if (settings.threshold.trim() && mode !== 'keyword') options.similarity_threshold = Number(settings.threshold)
  if (settings.fusion && mode === 'hybrid') {
    options.fusion = settings.fusion
    if (settings.fusion === 'weighted') options.alpha = settings.alpha
  }
  if (settings.rerank) options.rerank = true
  return Object.keys(options).length ? options : undefined
}

function validSettings(settings: AdvancedSettings): boolean {
  const candidates = settings.candidates.trim()
  const threshold = settings.threshold.trim()
  return (
    (!candidates || (Number.isInteger(Number(candidates)) && Number(candidates) >= 1 && Number(candidates) <= 100)) &&
    (!threshold || (Number(threshold) >= 0 && Number(threshold) <= 1))
  )
}

function AdvancedSearchSettings({
  settings,
  mode,
  onChange,
}: {
  settings: AdvancedSettings
  mode: SearchMode
  onChange: (settings: AdvancedSettings) => void
}) {
  const overridden = toSearchOptions(settings, mode) !== undefined
  return (
    <details className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800" data-testid="advanced-search">
      <summary className="cursor-pointer text-xs font-medium text-slate-600 dark:text-slate-400">
        Advanced settings{overridden ? ' (customised)' : ''}
      </summary>
      <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
        Explore how retrieval behaves on your documents. Leave a field empty to use the server setting. Chat answers always
        use the server settings.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor="adv-candidates" className="text-xs font-medium">
            Candidates per retriever
          </label>
          <input
            id="adv-candidates"
            type="number"
            min={1}
            max={100}
            step={1}
            inputMode="numeric"
            placeholder="Server setting"
            value={settings.candidates}
            onChange={(e) => onChange({ ...settings, candidates: e.target.value })}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="adv-threshold" className="text-xs font-medium">
            Similarity threshold
          </label>
          <input
            id="adv-threshold"
            type="number"
            min={0}
            max={1}
            step={0.05}
            placeholder="Server setting"
            disabled={mode === 'keyword'}
            value={settings.threshold}
            onChange={(e) => onChange({ ...settings, threshold: e.target.value })}
            className={`${inputClass} disabled:opacity-50`}
          />
        </div>
        <div>
          <label htmlFor="adv-fusion" className="text-xs font-medium">
            Fusion (hybrid)
          </label>
          <select
            id="adv-fusion"
            disabled={mode !== 'hybrid'}
            value={settings.fusion}
            onChange={(e) => onChange({ ...settings, fusion: e.target.value as AdvancedSettings['fusion'] })}
            className={`${inputClass} disabled:opacity-50`}
          >
            <option value="">Server setting</option>
            <option value="rrf">{FUSION_LABELS.rrf}</option>
            <option value="weighted">{FUSION_LABELS.weighted}</option>
          </select>
        </div>
      </div>
      {mode === 'hybrid' && settings.fusion === 'weighted' && (
        <div className="mt-3">
          <label htmlFor="adv-alpha" className="text-xs font-medium">
            Weight: semantic {Math.round(settings.alpha * 100)}% · keyword {Math.round((1 - settings.alpha) * 100)}%
          </label>
          <input
            id="adv-alpha"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={settings.alpha}
            onChange={(e) => onChange({ ...settings, alpha: Number(e.target.value) })}
            className="block w-full accent-brand-600"
          />
        </div>
      )}
      <label className="mt-3 flex items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={settings.rerank}
          onChange={(e) => onChange({ ...settings, rerank: e.target.checked })}
          className="mt-0.5 accent-brand-600"
        />
        <span>
          <span className="font-medium">Rerank results</span>
          <span className="block text-slate-600 dark:text-slate-400">
            Re-score the top candidates with the server&apos;s reranker, as chat answers do. Slower.
          </span>
        </span>
      </label>
      {overridden && (
        <button
          type="button"
          onClick={() => onChange(NO_OVERRIDES)}
          className="mt-2 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
        >
          Reset to server settings
        </button>
      )}
    </details>
  )
}

function location(hit: SearchHit): string {
  const parts = [hit.page_number ? `Page ${hit.page_number}` : null, hit.section].filter(Boolean)
  return parts.length ? parts.join(' · ') : `Chunk ${hit.chunk_index + 1}`
}

function ResultCard({
  hit,
  rank,
  query,
  reranker,
  onView,
}: {
  hit: SearchHit
  rank: number
  query: string
  reranker: string | null
  onView: () => void
}) {
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            <span className="mr-2 text-slate-500 dark:text-slate-400">#{rank}</span>
            {hit.filename}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{location(hit)}</p>
        </div>
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
      </div>
      <p className="mt-3 line-clamp-6 text-sm whitespace-pre-line text-slate-700 dark:text-slate-300">
        {highlight(hit.content, query)}
      </p>
      <button
        type="button"
        onClick={onView}
        className="mt-2 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
        aria-label={`View result ${rank} in context`}
      >
        View in context
      </button>
    </li>
  )
}

export function SearchPanel({ knowledgeBaseId, hasIndexedDocuments }: { knowledgeBaseId: string; hasIndexedDocuments: boolean }) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [fileTypes, setFileTypes] = useState<string[]>([])
  const [advanced, setAdvanced] = useState<AdvancedSettings>(NO_OVERRIDES)
  const settingsValid = validSettings(advanced)
  const [response, setResponse] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [searching, setSearching] = useState(false)
  const [viewing, setViewing] = useState<ViewedSource | null>(null)
  const closeViewer = useCallback(() => setViewing(null), [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!query.trim() || !settingsValid) return
    setSearching(true)
    setError(null)
    try {
      setResponse(
        await search({
          query: query.trim(),
          knowledge_base_ids: [knowledgeBaseId],
          mode,
          filters: fileTypes.length ? { file_types: fileTypes } : undefined,
          options: toSearchOptions(advanced, mode),
        }),
      )
    } catch (err) {
      setResponse(null)
      setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Search failed.', null))
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="space-y-5">
      <form onSubmit={handleSubmit} className="space-y-3" role="search">
        <div className="flex gap-2">
          <label htmlFor="kb-search" className="sr-only">
            Search this knowledge base
          </label>
          <input
            id="kb-search"
            type="search"
            value={query}
            maxLength={2000}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask something, e.g. How many days of annual leave do I get?"
            className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900"
          />
          <Button type="submit" loading={searching} disabled={!query.trim() || !settingsValid}>
            Search
          </Button>
        </div>
        <fieldset className="flex flex-wrap gap-1.5">
          <legend className="sr-only">Search mode</legend>
          {MODES.map((option) => (
            <label
              key={option.value}
              title={option.hint}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs font-medium transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-brand-500/40 ${
                mode === option.value
                  ? 'border-brand-600 bg-brand-600 text-white'
                  : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
              }`}
            >
              <input
                type="radio"
                name="search-mode"
                value={option.value}
                checked={mode === option.value}
                onChange={() => setMode(option.value)}
                className="sr-only"
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="mr-1 float-left text-xs text-slate-600 dark:text-slate-400">File types</legend>
          {FILE_TYPES.map((type) => {
            const on = fileTypes.includes(type.value)
            return (
              <button
                key={type.value}
                type="button"
                aria-pressed={on}
                onClick={() => setFileTypes(on ? fileTypes.filter((t) => t !== type.value) : [...fileTypes, type.value])}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  on
                    ? 'border-brand-600 bg-brand-50 text-brand-700 dark:border-brand-400 dark:bg-brand-500/15 dark:text-brand-100'
                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
                }`}
              >
                {type.label}
              </button>
            )
          })}
          {fileTypes.length === 0 && <span className="text-xs text-slate-500 dark:text-slate-400">all</span>}
        </fieldset>
        <AdvancedSearchSettings settings={advanced} mode={mode} onChange={setAdvanced} />
        {!settingsValid && (
          <p className="text-xs text-red-700 dark:text-red-300" role="alert">
            Candidates must be a whole number from 1 to 100, and the similarity threshold between 0 and 1.
          </p>
        )}
      </form>

      {!hasIndexedDocuments && (
        <p className="text-sm text-slate-500 dark:text-slate-400">No documents are indexed yet. Upload documents and wait for them to finish processing.</p>
      )}

      {error && <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>}

      {response && (
        <section aria-label="Search results" className="space-y-3">
          {response.results.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white px-5 py-8 text-center dark:border-slate-800 dark:bg-slate-900">
              <p className="text-sm font-medium">No relevant passages found</p>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {response.filtered_out > 0
                  ? `${response.filtered_out} candidate${response.filtered_out === 1 ? ' was' : 's were'} below the similarity threshold (${response.similarity_threshold}).`
                  : 'Nothing in this knowledge base matched the query.'}
              </p>
            </div>
          ) : (
            <ol className="space-y-3">
              {response.results.map((hit, index) => (
                <ResultCard
                  key={hit.chunk_id}
                  hit={hit}
                  rank={index + 1}
                  query={response.query}
                  reranker={response.reranker ?? null}
                  onView={() =>
                    setViewing({
                      chunkId: hit.chunk_id,
                      documentId: hit.document_id,
                      filename: hit.filename,
                      pageNumber: hit.page_number,
                      section: hit.section,
                      label: `Result ${index + 1}`,
                    })
                  }
                />
              ))}
            </ol>
          )}
          <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="search-diagnostics">
            {response.vector_candidates} semantic + {response.keyword_candidates} keyword candidates
            {response.filtered_out > 0 && `, ${response.filtered_out} below threshold`}
            {response.duplicates_removed ? `, ${response.duplicates_removed} near-duplicate${response.duplicates_removed === 1 ? '' : 's'} removed` : ''}
            {response.filter_documents !== undefined && response.filter_documents !== null
              ? `, ${response.filter_documents} document${response.filter_documents === 1 ? '' : 's'} matched the filters`
              : ''}{' '}
            ·{' '}
            {Object.entries(response.timings_ms)
              .filter(([name]) => name in TIMING_LABELS)
              .map(([name, ms]) => `${TIMING_LABELS[name]} ${ms.toFixed(ms < 10 ? 1 : 0)} ms`)
              .join(' · ')}
          </p>
          {response.parameters && (
            <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="search-parameters">
              Used: {response.parameters.candidates} candidates per retriever
              {response.mode !== 'keyword' && ` · similarity ≥ ${response.parameters.similarity_threshold}`}
              {response.mode === 'hybrid' &&
                ` · ${FUSION_LABELS[response.parameters.fusion]}${
                  response.parameters.fusion === 'weighted' ? ` (semantic weight ${response.parameters.alpha})` : ''
                }`}
              {response.reranker && ` · reranked by ${response.reranker}`}
            </p>
          )}
          {response.parameters?.rerank && !response.reranker && response.results.length > 0 && (
            <p className="text-xs text-amber-800 dark:text-amber-300" data-testid="rerank-unavailable">
              Not reranked: the reranker is turned off or unavailable, so results keep the retrieval order and have no
              rerank scores.
            </p>
          )}
        </section>
      )}
      {viewing && <SourceViewer source={viewing} onClose={closeViewer} />}
    </div>
  )
}
