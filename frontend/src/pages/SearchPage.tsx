import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'

import { SourceViewer, type ViewedSource } from '../components/citations/SourceViewer'
import { SearchResultCard } from '../components/search/SearchResultCard'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { useResource } from '../hooks/useResource'
import { ApiError } from '../services/api'
import { listKnowledgeBases } from '../services/knowledgeBases'
import { search, type SearchHit, type SearchResponse } from '../services/retrieval'
import { pluralize } from '../utils/format'

// The reranker scores up to RERANK_CANDIDATES (20 by default) passages; ask for all of them.
const RESULT_LIMIT = 20

const fetchKnowledgeBases = () => listKnowledgeBases()

/** Search every knowledge base at once and see how relevant each passage is. */
export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const submitted = params.get('q') ?? ''
  const scope = params.get('kb') ?? ''
  const [query, setQuery] = useState(submitted)
  const [knowledgeBaseId, setKnowledgeBaseId] = useState(scope)
  // The outcome of the search the URL describes, keyed so a stale one is never shown.
  const key = `${scope}|${submitted.trim()}`
  const [outcome, setOutcome] = useState<{ key: string; response?: SearchResponse; error?: ApiError } | null>(null)
  const current = submitted.trim() && outcome?.key === key ? outcome : null
  const response = current?.response ?? null
  const error = current?.error ?? null
  const searching = Boolean(submitted.trim()) && !current
  const [viewing, setViewing] = useState<ViewedSource | null>(null)
  const closeViewer = useCallback(() => setViewing(null), [])
  const { data: knowledgeBases } = useResource(fetchKnowledgeBases)
  const kbNames = new Map((knowledgeBases ?? []).map((kb) => [kb.id, kb.name]))

  // The URL holds the search, so results survive a reload and can be bookmarked.
  useEffect(() => {
    if (!submitted.trim()) return
    let cancelled = false
    search({
      query: submitted.trim(),
      knowledge_base_ids: scope ? [scope] : [],
      limit: RESULT_LIMIT,
      options: { rerank: true, topic: true },
    })
      .then((result) => !cancelled && setOutcome({ key, response: result }))
      .catch((err) => {
        if (!cancelled) {
          setOutcome({ key, error: err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Search failed.', null) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [key, submitted, scope])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!query.trim()) return
    setParams(knowledgeBaseId ? { q: query.trim(), kb: knowledgeBaseId } : { q: query.trim() })
  }

  const open = (hit: SearchHit, rank: number) =>
    setViewing({
      chunkId: hit.chunk_id,
      documentId: hit.document_id,
      filename: hit.filename,
      pageNumber: hit.page_number,
      section: hit.section,
      label: `Result ${rank}`,
    })

  const results = response?.results ?? []
  const labelled = results.some((hit) => hit.relevance)
  const relevant = labelled ? results.filter((hit) => hit.relevance !== 'low') : results
  const lessRelevant = labelled ? results.filter((hit) => hit.relevance === 'low') : []
  const card = (hit: SearchHit, rank: number) => (
    <SearchResultCard
      key={hit.chunk_id}
      hit={hit}
      rank={rank}
      query={response!.query}
      reranker={response!.reranker ?? null}
      knowledgeBaseName={kbNames.get(hit.knowledge_base_id)}
      variant="simple"
      onView={() => open(hit, rank)}
    />
  )

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-8 sm:py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Search My Knowledge</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Find every passage about a topic across your knowledge bases, ranked by relevance. Open a result to read it in its
          document.
        </p>
      </header>

      <form onSubmit={handleSubmit} role="search" className="mt-6 flex flex-col gap-2 sm:flex-row">
        <label htmlFor="knowledge-search" className="sr-only">
          What are you looking for?
        </label>
        <input
          id="knowledge-search"
          type="search"
          value={query}
          maxLength={2000}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Find everything related to expense approvals"
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900"
        />
        <label htmlFor="knowledge-scope" className="sr-only">
          Knowledge base to search
        </label>
        <select
          id="knowledge-scope"
          value={knowledgeBaseId}
          onChange={(e) => setKnowledgeBaseId(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 sm:max-w-56 dark:border-slate-700 dark:bg-slate-900"
        >
          <option value="">All knowledge bases</option>
          {(knowledgeBases ?? []).map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name}
            </option>
          ))}
        </select>
        <Button type="submit" loading={searching} disabled={!query.trim()}>
          Search
        </Button>
      </form>

      {knowledgeBases && knowledgeBases.length === 0 && (
        <p className="mt-6 text-sm text-slate-600 dark:text-slate-400">
          You have no knowledge bases yet. <Link to="/knowledge-bases" className="font-medium text-brand-600 hover:underline dark:text-brand-300">Create one</Link> and upload documents to search them here.
        </p>
      )}

      {error && (
        <div className="mt-6">
          <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>
        </div>
      )}

      {response && (
        <section className="mt-6 space-y-3" aria-live="polite" aria-busy={searching}>
          <h2 className="text-sm font-semibold" data-testid="result-count">
            {labelled ? pluralize(relevant.length, 'relevant result') : pluralize(results.length, 'result')}
            {scope && kbNames.get(scope) ? ` in ${kbNames.get(scope)}` : ''}
          </h2>
          {response.query !== submitted.trim().split(/\s+/).join(' ') && (
            <p className="text-xs text-slate-600 dark:text-slate-400" data-testid="searched-for">
              Searched for “{response.query}”
            </p>
          )}
          {!response.reranker && results.length > 0 && (
            <p className="text-xs text-slate-600 dark:text-slate-400" data-testid="relevance-unavailable">
              Relevance isn&apos;t rated: the reranker is turned off or unavailable, so results are in retrieval order.
            </p>
          )}
          {response.reranker && !labelled && results.length > 0 && (
            <p className="text-xs text-slate-600 dark:text-slate-400" data-testid="relevance-unavailable">
              Relevance isn&apos;t rated for the {response.reranker} reranker yet; results are ranked by its scores.
            </p>
          )}
          {results.length === 0 ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">No passages matched. Try other words, or check that your documents have finished processing.</p>
          ) : (
            <>
              {relevant.length > 0 && <ol className="space-y-3">{relevant.map((hit) => card(hit, results.indexOf(hit) + 1))}</ol>}
              {lessRelevant.length > 0 && (
                <details className="rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-800" open={relevant.length === 0}>
                  <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-400">
                    {pluralize(lessRelevant.length, 'less relevant result')}
                  </summary>
                  <ol className="mt-3 space-y-3">{lessRelevant.map((hit) => card(hit, results.indexOf(hit) + 1))}</ol>
                </details>
              )}
            </>
          )}
        </section>
      )}

      {viewing && <SourceViewer source={viewing} onClose={closeViewer} />}
    </div>
  )
}
