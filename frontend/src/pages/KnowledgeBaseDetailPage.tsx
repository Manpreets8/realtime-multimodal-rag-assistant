import { useCallback, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { StatusBadge } from '../components/documents/StatusBadge'
import { UploadDropzone } from '../components/documents/UploadDropzone'
import { KnowledgeBaseFormDialog } from '../components/knowledge-bases/KnowledgeBaseFormDialog'
import { AskPanel } from '../components/knowledge-bases/AskPanel'
import { SearchPanel } from '../components/knowledge-bases/SearchPanel'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { useDocumentUploads } from '../hooks/useDocumentUploads'
import { usePolling } from '../hooks/usePolling'
import { useResource } from '../hooks/useResource'
import { useToast } from '../hooks/useToast'
import { ApiError } from '../services/api'
import {
  STATUS_POLL_INTERVAL_MS,
  WORKER_WARNING_AFTER_MS,
  deleteDocument,
  downloadDocument,
  getUploadConfig,
  isPending,
  listDocuments,
  reprocessDocument,
  type DocumentItem,
  type IngestionProgress,
} from '../services/documents'
import { getReadiness } from '../services/health'
import { deleteKnowledgeBase, getKnowledgeBase, updateKnowledgeBase } from '../services/knowledgeBases'
import { formatBytes, formatDateTime, pluralize } from '../utils/format'

function ActionIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-4 sm:size-3.5" aria-hidden>
      <path d={path} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

type TabId = 'documents' | 'ask' | 'search'
const TABS: { id: TabId; label: string }[] = [
  { id: 'documents', label: 'Documents' },
  { id: 'ask', label: 'Ask' },
  { id: 'search', label: 'Search' },
]

function DocumentDetails({ document }: { document: DocumentItem }) {
  const type = document.extension.slice(1).toUpperCase()
  if (document.status === 'failed') {
    return (
      <p className="line-clamp-2 text-xs text-rose-600 dark:text-rose-400" title={document.error_message ?? undefined}>
        {document.error_message ?? 'Processing failed.'}
      </p>
    )
  }
  const parts = [type]
  if (document.status === 'completed') {
    parts.push(pluralize(document.chunk_count, 'chunk'))
    if (document.page_count) parts.push(pluralize(document.page_count, 'page'))
  } else if (document.status === 'processing') {
    return <ProcessingDetails type={type} progress={document.progress ?? null} />
  }
  return <p className="truncate text-xs text-slate-500 dark:text-slate-400">{parts.join(' · ')}</p>
}

const STAGE_LABELS: Record<IngestionProgress['stage'], string> = {
  extracting: 'Extracting text…',
  chunking: 'Splitting into chunks…',
  embedding: 'Embedding',
  saving: 'Saving to the index…',
}

function ProcessingDetails({ type, progress }: { type: string; progress: IngestionProgress | null }) {
  if (!progress) return <p className="truncate text-xs text-slate-500 dark:text-slate-400">{type} · Extracting and indexing…</p>
  const embedding = progress.stage === 'embedding' && progress.total > 0
  const label = embedding ? `Embedding ${progress.done}/${progress.total} chunks` : STAGE_LABELS[progress.stage]
  return (
    <div className="max-w-xs">
      <p className="truncate text-xs text-slate-500 dark:text-slate-400">
        {type} · {label}
      </p>
      {embedding && (
        <div
          role="progressbar"
          aria-label={`Embedding ${progress.done} of ${progress.total} chunks`}
          aria-valuemin={0}
          aria-valuemax={progress.total}
          aria-valuenow={progress.done}
          className="mt-1 h-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
        >
          <div className="h-full rounded-full bg-amber-500 transition-[width] duration-500" style={{ width: `${(100 * progress.done) / progress.total}%` }} />
        </div>
      )}
    </div>
  )
}

export default function KnowledgeBaseDetailPage() {
  const { kbId = '' } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab: TabId = TABS.find((tab) => tab.id === searchParams.get('tab'))?.id ?? 'documents'

  const kb = useResource(useCallback(() => getKnowledgeBase(kbId), [kbId]))
  const documents = useResource(useCallback(() => listDocuments(kbId), [kbId]))
  const { data: uploadConfig } = useResource(getUploadConfig)

  const [editing, setEditing] = useState(false)
  const [deletingKb, setDeletingKb] = useState(false)
  const [deletingDocument, setDeletingDocument] = useState<DocumentItem | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const setDocuments = documents.setData
  const setKb = kb.setData
  const onUploaded = useCallback(
    (document: DocumentItem) => {
      setDocuments((prev) => [document, ...(prev ?? [])])
      setKb((prev) => (prev ? { ...prev, document_count: prev.document_count + 1 } : prev))
    },
    [setDocuments, setKb],
  )
  const uploads = useDocumentUploads(kbId, uploadConfig, onUploaded)

  // Ingestion runs in the background: refresh statuses while any document is queued or processing.
  const pendingCount = documents.data?.filter(isPending).length ?? 0
  const refreshDocuments = useCallback(async () => setDocuments(await listDocuments(kbId)), [kbId, setDocuments])
  usePolling(refreshDocuments, pendingCount > 0, STATUS_POLL_INTERVAL_MS)

  // Documents are processed by a separate worker process. If one has been waiting a while,
  // ask the server whether any worker is alive, so a missing worker isn't a silent stall.
  const queuedTimes = documents.data?.filter((d) => d.status === 'uploaded').map((d) => Date.parse(d.created_at)) ?? []
  const oldestQueued = queuedTimes.length ? Math.min(...queuedTimes) : null
  const [workerMissing, setWorkerMissing] = useState(false)
  const checkWorkers = useCallback(async () => {
    if (oldestQueued === null || Date.now() - oldestQueued < WORKER_WARNING_AFTER_MS) {
      setWorkerMissing(false)
      return
    }
    setWorkerMissing((await getReadiness()).services?.ingestion_workers === 0)
  }, [oldestQueued])
  usePolling(checkWorkers, oldestQueued !== null, STATUS_POLL_INTERVAL_MS)
  const noWorker = workerMissing && oldestQueued !== null

  if (kb.error) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-8">
        <ErrorAlert requestId={kb.error.status >= 500 ? kb.error.requestId : null}>
          {kb.error.status === 404 ? 'This knowledge base does not exist or you do not have access to it.' : kb.error.message}
        </ErrorAlert>
        <Link to="/knowledge-bases" className="mt-4 inline-block text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300">
          ← Back to knowledge bases
        </Link>
      </div>
    )
  }

  async function handleRetry(document: DocumentItem) {
    setActionError(null)
    try {
      const updated = await reprocessDocument(document.id)
      setDocuments((prev) => prev?.map((d) => (d.id === updated.id ? updated : d)) ?? null)
      toast.success(`Processing “${document.filename}” again.`)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not retry processing.')
    }
  }

  async function handleDownload(document: DocumentItem) {
    setActionError(null)
    try {
      await downloadDocument(document)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Download failed.')
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8 sm:py-10">
      <nav className="text-sm text-slate-500 dark:text-slate-400" aria-label="Breadcrumb">
        <Link to="/knowledge-bases" className="hover:text-slate-700 dark:hover:text-slate-300">
          Knowledge bases
        </Link>
        <span className="mx-2">/</span>
        <span className="text-slate-700 dark:text-slate-300">{kb.data?.name ?? '…'}</span>
      </nav>

      <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight">{kb.data?.name ?? 'Loading…'}</h1>
          {kb.data?.description && (
            <p className="mt-1 max-w-2xl text-sm text-slate-600 dark:text-slate-400">{kb.data.description}</p>
          )}
        </div>
        <div className="flex gap-2">
          <Button onClick={() => navigate(`/chat?kb=${encodeURIComponent(kbId)}`)} disabled={!kb.data}>
            Chat
          </Button>
          <Button variant="secondary" onClick={() => setEditing(true)} disabled={!kb.data}>
            Edit
          </Button>
          <Button variant="secondary" onClick={() => setDeletingKb(true)} disabled={!kb.data} className="text-rose-600 dark:text-rose-400">
            Delete
          </Button>
        </div>
      </header>

      <div className="mt-8 border-b border-slate-200 dark:border-slate-800" role="tablist" aria-label="Knowledge base views">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`panel-${tab.id}`}
            onClick={() => setSearchParams(tab.id === 'documents' ? {} : { tab: tab.id }, { replace: true })}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? 'border-brand-600 text-brand-700 dark:text-brand-100'
                : 'border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'ask' ? (
        <div className="mt-6" role="tabpanel" id="panel-ask" aria-labelledby="tab-ask">
          <AskPanel knowledgeBaseId={kbId} />
        </div>
      ) : activeTab === 'search' ? (
        <div className="mt-6" role="tabpanel" id="panel-search" aria-labelledby="tab-search">
          <SearchPanel knowledgeBaseId={kbId} hasIndexedDocuments={documents.data?.some((d) => d.status === 'completed') ?? false} />
        </div>
      ) : (
        <div role="tabpanel" id="panel-documents" aria-labelledby="tab-documents">
        <section className="mt-6" aria-labelledby="upload-heading">
          <h2 id="upload-heading" className="mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Upload documents
          </h2>
          <UploadDropzone config={uploadConfig} items={uploads.items} onFiles={uploads.addFiles} onDismiss={uploads.dismiss} />
        </section>
  
        <section className="mt-10" aria-labelledby="documents-heading">
          <div className="mb-3 flex items-baseline justify-between gap-4">
            <h2 id="documents-heading" className="text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Documents{' '}
              {kb.data && (
                <span className="font-normal normal-case">
                  ({pluralize(kb.data.document_count, 'file')}
                  {pendingCount > 0 && ` · ${pendingCount} processing`})
                </span>
              )}
            </h2>
          </div>
  
          {actionError && (
            <div className="mb-3">
              <ErrorAlert>{actionError}</ErrorAlert>
            </div>
          )}

          {noWorker && (
            <div
              role="status"
              className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
            >
              <p className="font-medium">Documents are waiting for an ingestion worker.</p>
              <p className="mt-0.5 text-xs">
                No worker is running, so queued files won't be processed yet. Start one with{' '}
                <code className="rounded bg-amber-100 px-1 dark:bg-amber-900/60">python -m app.workers.ingestion_worker</code>; waiting
                documents are then processed automatically.
              </p>
            </div>
          )}
  
          {documents.error ? (
            <ErrorAlert requestId={documents.error.requestId}>{documents.error.message}</ErrorAlert>
          ) : documents.loading && !documents.data ? (
            <div className="h-24 animate-pulse rounded-xl bg-slate-200/60 dark:bg-slate-800/60" aria-label="Loading documents" />
          ) : documents.data?.length === 0 ? (
            <p className="rounded-xl border border-slate-200 bg-white px-6 py-10 text-center text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
              No documents yet. Upload a PDF, Word, text or Markdown file to get started.
            </p>
          ) : (
            <>
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 uppercase dark:border-slate-800 dark:text-slate-400">
                    <tr>
                      <th scope="col" className="px-4 py-3 font-medium">Name</th>
                      <th scope="col" className="px-2 py-3 font-medium sm:px-4">Status</th>
                      <th scope="col" className="hidden px-4 py-3 font-medium sm:table-cell">Size</th>
                      <th scope="col" className="hidden px-4 py-3 font-medium md:table-cell">Uploaded</th>
                      <th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {documents.data?.map((document) => (
                      <tr key={document.id}>
                        <td className="w-full max-w-0 py-3 pr-2 pl-4">
                          <p className="truncate font-medium" title={document.filename}>{document.filename}</p>
                          <DocumentDetails document={document} />
                        </td>
                        <td className="px-2 py-3 sm:px-4">
                          <StatusBadge status={document.status} title={document.error_message ?? undefined} />
                        </td>
                        <td className="hidden px-4 py-3 whitespace-nowrap text-slate-600 sm:table-cell dark:text-slate-400">
                          {formatBytes(document.size_bytes)}
                        </td>
                        <td className="hidden px-4 py-3 whitespace-nowrap text-slate-600 md:table-cell dark:text-slate-400">
                          {formatDateTime(document.created_at)}
                        </td>
                        <td className="px-2 py-3 text-right whitespace-nowrap sm:px-4">
                          {document.status === 'failed' && (
                            <button
                              type="button"
                              onClick={() => handleRetry(document)}
                              className="inline-flex items-center gap-1.5 rounded-md p-2 text-xs font-medium text-brand-600 hover:bg-brand-50 sm:px-2 sm:py-1 dark:text-brand-100 dark:hover:bg-brand-500/10"
                              aria-label={`Retry processing ${document.filename}`}
                            >
                              <ActionIcon path="M4 12a8 8 0 0114-5.3L20 9M20 4v5h-5M20 12a8 8 0 01-14 5.3L4 15M4 20v-5h5" />
                              <span className="hidden sm:inline">Retry</span>
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => handleDownload(document)}
                            className="inline-flex items-center gap-1.5 rounded-md p-2 text-xs font-medium text-slate-600 hover:bg-slate-100 sm:px-2 sm:py-1 dark:text-slate-300 dark:hover:bg-slate-800"
                            aria-label={`Download ${document.filename}`}
                          >
                            <ActionIcon path="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14" />
                            <span className="hidden sm:inline">Download</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setDeletingDocument(document)}
                            className="inline-flex items-center gap-1.5 rounded-md p-2 text-xs font-medium text-rose-600 hover:bg-rose-50 sm:px-2 sm:py-1 dark:text-rose-400 dark:hover:bg-rose-500/10"
                            aria-label={`Delete ${document.filename}`}
                          >
                            <ActionIcon path="M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                            <span className="hidden sm:inline">Delete</span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {pendingCount > 0 && (
                <p className="mt-3 text-xs text-slate-500 dark:text-slate-400" role="status">
                  Documents are extracted, split into chunks and embedded in the background. This page updates
                  automatically.
                </p>
              )}
            </>
          )}
        </section>
        </div>
      )}

      <KnowledgeBaseFormDialog
        open={editing}
        initial={kb.data ?? undefined}
        onClose={() => setEditing(false)}
        onSubmit={async (input) => {
          kb.setData(await updateKnowledgeBase(kbId, input))
          toast.success('Knowledge base saved.')
        }}
      />

      <ConfirmDialog
        open={deletingKb}
        title="Delete knowledge base?"
        description={
          <>
            <strong>{kb.data?.name}</strong> and its {pluralize(kb.data?.document_count ?? 0, 'document')} will be
            permanently deleted. This cannot be undone.
          </>
        }
        confirmLabel="Delete knowledge base"
        onClose={() => setDeletingKb(false)}
        onConfirm={async () => {
          await deleteKnowledgeBase(kbId)
          toast.success(`Deleted “${kb.data?.name ?? 'knowledge base'}”.`)
          navigate('/knowledge-bases', { replace: true })
        }}
      />

      <ConfirmDialog
        open={deletingDocument !== null}
        title="Delete document?"
        description={
          <>
            <strong>{deletingDocument?.filename}</strong> will be permanently removed from this knowledge base.
          </>
        }
        confirmLabel="Delete document"
        onClose={() => setDeletingDocument(null)}
        onConfirm={async () => {
          if (!deletingDocument) return
          await deleteDocument(deletingDocument.id)
          documents.setData((prev) => prev?.filter((d) => d.id !== deletingDocument.id) ?? null)
          kb.setData((prev) => (prev ? { ...prev, document_count: Math.max(0, prev.document_count - 1) } : prev))
          toast.success(`Deleted “${deletingDocument.filename}”.`)
        }}
      />
    </div>
  )
}
