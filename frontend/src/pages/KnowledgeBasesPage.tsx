import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { KnowledgeBaseFormDialog } from '../components/knowledge-bases/KnowledgeBaseFormDialog'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Skeleton } from '../components/ui/Skeleton'
import { useResource } from '../hooks/useResource'
import { useToast } from '../hooks/useToast'
import {
  createKnowledgeBase,
  listKnowledgeBases,
  type KnowledgeBase,
  type KnowledgeBaseSort,
} from '../services/knowledgeBases'
import { formatBytes, formatRelative, pluralize } from '../utils/format'

const SEARCH_DELAY_MS = 250

const SORTS: { value: KnowledgeBaseSort; label: string }[] = [
  { value: 'recent', label: 'Recent activity' },
  { value: 'name', label: 'Name (A–Z)' },
  { value: 'created', label: 'Newest first' },
]

function processing(kb: KnowledgeBase): number {
  return (kb.status_counts.uploaded ?? 0) + (kb.status_counts.processing ?? 0)
}

function KnowledgeBaseCard({ kb }: { kb: KnowledgeBase }) {
  const failed = kb.status_counts.failed ?? 0
  const busy = processing(kb)
  return (
    <Link
      to={`/knowledge-bases/${kb.id}`}
      className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5 transition hover:border-brand-500/50 hover:shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 className="truncate font-semibold">{kb.name}</h2>
      <p className="mt-1 line-clamp-2 flex-1 text-sm text-slate-500 dark:text-slate-400">
        {kb.description || 'No description'}
      </p>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
        {[
          ['Documents', kb.document_count],
          ['Passages', kb.passage_count],
          ['Chats', kb.conversation_count],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg bg-slate-50 px-2 py-1.5 dark:bg-slate-800/60">
            <dt className="text-[11px] text-slate-500 dark:text-slate-400">{label}</dt>
            <dd className="text-sm font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
        <span>
          {formatBytes(kb.total_bytes)}
          {busy > 0 && <span className="text-amber-700 dark:text-amber-400"> · {busy} processing</span>}
          {failed > 0 && <span className="text-rose-700 dark:text-rose-300"> · {failed} failed</span>}
        </span>
        <span>Active {formatRelative(kb.last_activity_at ?? kb.updated_at)}</span>
      </p>
    </Link>
  )
}

export default function KnowledgeBasesPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<KnowledgeBaseSort>('recent')
  const [creating, setCreating] = useState(false)

  // Search as you type, once typing pauses.
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [query])

  const fetchList = useCallback(() => listKnowledgeBases({ search, sort }), [search, sort])
  const { data: knowledgeBases, error, loading, reload } = useResource(fetchList)
  const hasAny = Boolean(search) || (knowledgeBases?.length ?? 0) > 0

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Knowledge bases</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Separate collections of documents. A chat answers only from the knowledge base you select.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>New knowledge base</Button>
      </header>

      {hasAny && (
        <div className="mt-6 flex flex-wrap items-center gap-3" role="search">
          <label htmlFor="kb-search" className="sr-only">
            Search knowledge bases
          </label>
          <input
            id="kb-search"
            type="search"
            value={query}
            maxLength={100}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name or description"
            className="block w-full max-w-sm rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:placeholder:text-slate-400"
          />
          <label htmlFor="kb-sort" className="text-sm text-slate-600 dark:text-slate-400">
            Sort by
          </label>
          <select
            id="kb-sort"
            value={sort}
            onChange={(event) => setSort(event.target.value as KnowledgeBaseSort)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          >
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="mt-6" aria-busy={loading}>
        {loading && !knowledgeBases ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Loading knowledge bases">
            {[0, 1, 2].map((i) => (
              <div key={i} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
                <Skeleton className="h-5 w-2/3" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="space-y-3">
            <ErrorAlert requestId={error.requestId}>{error.message}</ErrorAlert>
            <Button variant="secondary" onClick={reload}>
              Try again
            </Button>
          </div>
        ) : knowledgeBases && knowledgeBases.length === 0 && search ? (
          <p className="rounded-xl border border-dashed border-slate-300 px-6 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
            No knowledge bases match “{search}”.
          </p>
        ) : knowledgeBases && knowledgeBases.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center dark:border-slate-700 dark:bg-slate-900">
            <h2 className="text-base font-semibold">No knowledge bases yet</h2>
            <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Create one for each topic, such as machine learning, interview preparation or company policies, then upload
              documents to it.
            </p>
            <Button className="mt-5" onClick={() => setCreating(true)}>
              Create your first knowledge base
            </Button>
          </div>
        ) : (
          <>
            <p className="sr-only" role="status">
              {pluralize(knowledgeBases?.length ?? 0, 'knowledge base')}
            </p>
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {knowledgeBases?.map((kb) => (
                <li key={kb.id}>
                  <KnowledgeBaseCard kb={kb} />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <KnowledgeBaseFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSubmit={async (input) => {
          const created = await createKnowledgeBase(input)
          toast.success(`Created “${created.name}”. Upload documents to start asking questions.`)
          navigate(`/knowledge-bases/${created.id}`)
        }}
      />
    </div>
  )
}
