import { useCallback, useState, type FormEvent } from 'react'
import { Link } from 'react-router'

import { StatusBadge } from '../components/documents/StatusBadge'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { Skeleton } from '../components/ui/Skeleton'
import { usePolling } from '../hooks/usePolling'
import { useResource } from '../hooks/useResource'
import { useToast } from '../hooks/useToast'
import { ApiError } from '../services/api'
import {
  deleteDocument,
  listAllDocuments,
  reprocessDocument,
  type DocumentListItem,
  type DocumentStatus,
} from '../services/documents'
import { formatBytes, formatRelative, pluralize } from '../utils/format'

const PAGE_SIZE = 25
const POLL_MS = 3000

const FILTERS: { value: DocumentStatus | undefined; label: string }[] = [
  { value: undefined, label: 'All' },
  { value: 'completed', label: 'Indexed' },
  { value: 'processing', label: 'Processing' },
  { value: 'uploaded', label: 'Queued' },
  { value: 'failed', label: 'Failed' },
]

function details(document: DocumentListItem): string {
  const parts = [formatBytes(document.size_bytes)]
  if (document.page_count) parts.push(pluralize(document.page_count, 'page'))
  if (document.status === 'completed') parts.push(pluralize(document.chunk_count, 'passage'))
  return parts.join(' · ')
}

export default function DocumentsPage() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<DocumentStatus | undefined>(undefined)
  const [offset, setOffset] = useState(0)
  const [deleting, setDeleting] = useState<DocumentListItem | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const fetchPage = useCallback(
    () => listAllDocuments({ status, search, limit: PAGE_SIZE, offset }),
    [status, search, offset],
  )
  const { data, error, loading, reload, setData } = useResource(fetchPage)
  const rows = data?.items ?? []
  const total = data?.total ?? 0

  // Keep statuses live while anything on this page is queued or processing.
  const active = rows.some((row) => row.status === 'uploaded' || row.status === 'processing')
  usePolling(async () => setData(await fetchPage()), active, POLL_MS)

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setSearch(query.trim())
  }

  function chooseFilter(value: DocumentStatus | undefined) {
    setOffset(0)
    setStatus(value)
  }

  async function reprocess(document: DocumentListItem) {
    setBusyId(document.id)
    try {
      const updated = await reprocessDocument(document.id)
      setData((current) =>
        current
          ? { ...current, items: current.items.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)) }
          : current,
      )
      toast.success(`Processing “${document.filename}” again.`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The document could not be queued.')
    } finally {
      setBusyId(null)
    }
  }

  const filtered = Boolean(search || status)

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Every document across your knowledge bases. Upload new files from a knowledge base.
        </p>
      </header>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <form onSubmit={submitSearch} role="search" className="flex w-full max-w-sm gap-2">
          <label htmlFor="documents-search" className="sr-only">
            Search documents by filename
          </label>
          <input
            id="documents-search"
            type="search"
            value={query}
            maxLength={255}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by filename"
            className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:placeholder:text-slate-400"
          />
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-1.5">
          {FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              aria-pressed={status === filter.value}
              onClick={() => chooseFilter(filter.value)}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                status === filter.value
                  ? 'border-brand-600 bg-brand-600 text-white'
                  : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      {error && !data && (
        <div className="mt-6 space-y-3">
          <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>
          <Button variant="secondary" onClick={reload}>
            Try again
          </Button>
        </div>
      )}

      <section aria-label="Documents" aria-busy={loading} className="mt-6">
        {loading && !data ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-16 w-full rounded-xl" />
            ))}
          </div>
        ) : data && rows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 px-6 py-12 text-center dark:border-slate-700">
            <p className="text-sm font-medium">{filtered ? 'No documents match' : 'No documents yet'}</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              {filtered ? (
                'Try another filename or status.'
              ) : (
                <>
                  Create a knowledge base and upload PDF, Word, text or Markdown files.{' '}
                  <Link to="/knowledge-bases" className="font-medium text-brand-600 hover:underline dark:text-brand-300">
                    Go to knowledge bases →
                  </Link>
                </>
              )}
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
            {rows.map((document) => (
              <li key={document.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <div className="min-w-0 flex-1 basis-60">
                  <Link
                    to={`/knowledge-bases/${document.knowledge_base_id}`}
                    className="block truncate text-sm font-medium hover:text-brand-700 dark:hover:text-brand-300"
                  >
                    {document.filename}
                  </Link>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                    {document.knowledge_base_name} · {details(document)} · {formatRelative(document.created_at)}
                  </p>
                  {document.status === 'failed' && document.error_message && (
                    <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{document.error_message}</p>
                  )}
                </div>
                <StatusBadge status={document.status} />
                <div className="flex gap-1">
                  {document.status === 'failed' && (
                    <Button
                      variant="ghost"
                      className="px-2 py-1 text-xs"
                      disabled={busyId === document.id}
                      onClick={() => void reprocess(document)}
                      aria-label={`Retry ${document.filename}`}
                    >
                      Retry
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    className="px-2 py-1 text-xs"
                    onClick={() => setDeleting(document)}
                    aria-label={`Delete ${document.filename}`}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {total > PAGE_SIZE && (
        <nav aria-label="Pagination" className="mt-4 flex items-center justify-between text-sm text-slate-600 dark:text-slate-400">
          <p>
            Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </p>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}>
              Previous
            </Button>
            <Button variant="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </nav>
      )}

      <ConfirmDialog
        open={deleting !== null}
        title="Delete this document?"
        description={`“${deleting?.filename ?? ''}” and its search index will be removed from ${deleting?.knowledge_base_name ?? 'its knowledge base'}. This cannot be undone.`}
        confirmLabel="Delete document"
        onConfirm={async () => {
          if (!deleting) return
          await deleteDocument(deleting.id)
          toast.success(`Deleted “${deleting.filename}”.`)
          reload()
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  )
}
