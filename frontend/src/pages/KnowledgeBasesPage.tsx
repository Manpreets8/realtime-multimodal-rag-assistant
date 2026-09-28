import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { KnowledgeBaseFormDialog } from '../components/knowledge-bases/KnowledgeBaseFormDialog'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { useResource } from '../hooks/useResource'
import { createKnowledgeBase, listKnowledgeBases } from '../services/knowledgeBases'
import { formatRelative, pluralize } from '../utils/format'

export default function KnowledgeBasesPage() {
  const navigate = useNavigate()
  const { data: knowledgeBases, error, loading, reload } = useResource(listKnowledgeBases)
  const [creating, setCreating] = useState(false)

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8 sm:py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Knowledge bases</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Collections of documents the assistant can answer questions from.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>New knowledge base</Button>
      </header>

      <div className="mt-8">
        {loading && !knowledgeBases ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading knowledge bases">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-32 animate-pulse rounded-xl bg-slate-200/60 dark:bg-slate-800/60" />
            ))}
          </div>
        ) : error ? (
          <div className="space-y-3">
            <ErrorAlert requestId={error.requestId}>{error.message}</ErrorAlert>
            <Button variant="secondary" onClick={reload}>
              Try again
            </Button>
          </div>
        ) : knowledgeBases && knowledgeBases.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center dark:border-slate-700 dark:bg-slate-900">
            <h2 className="text-base font-semibold">No knowledge bases yet</h2>
            <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Create one for each topic, such as company policies or project documentation, then upload documents to it.
            </p>
            <Button className="mt-5" onClick={() => setCreating(true)}>
              Create your first knowledge base
            </Button>
          </div>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {knowledgeBases?.map((kb) => (
              <li key={kb.id}>
                <Link
                  to={`/knowledge-bases/${kb.id}`}
                  className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5 transition hover:border-brand-500/50 hover:shadow-sm dark:border-slate-800 dark:bg-slate-900"
                >
                  <h2 className="truncate font-semibold">{kb.name}</h2>
                  <p className="mt-1 line-clamp-2 flex-1 text-sm text-slate-500 dark:text-slate-400">
                    {kb.description || 'No description'}
                  </p>
                  <p className="mt-4 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                    <span>{pluralize(kb.document_count, 'document')}</span>
                    <span>Updated {formatRelative(kb.updated_at)}</span>
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <KnowledgeBaseFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSubmit={async (input) => {
          const created = await createKnowledgeBase(input)
          navigate(`/knowledge-bases/${created.id}`)
        }}
      />
    </div>
  )
}
