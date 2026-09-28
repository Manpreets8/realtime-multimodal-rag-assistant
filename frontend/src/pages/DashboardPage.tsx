import { Link } from 'react-router'

import { SystemStatusCard } from '../components/SystemStatusCard'
import { useAuth } from '../hooks/useAuth'
import { useResource } from '../hooks/useResource'
import { listConversations } from '../services/chat'
import { listKnowledgeBases, type KnowledgeBase } from '../services/knowledgeBases'
import { formatRelative, pluralize } from '../utils/format'

const NEXT_STEPS = [
  { title: 'Create a knowledge base', text: 'Group related documents, such as company policies or project docs.' },
  { title: 'Upload documents', text: 'Add PDF, Word, text and Markdown files to a knowledge base.' },
  { title: 'Ask questions', text: 'Chat with a knowledge base and get answers with source citations, by text or voice.' },
]

const SECTION_TITLE = 'mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400'
const CARD = 'rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900'
const LINK = 'text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300'

function Stat({ label, value, detail, to }: { label: string; value: number | string; detail?: string; to: string }) {
  return (
    <Link to={to} className={`${CARD} block p-4 transition-colors hover:border-brand-500/50`}>
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {detail && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{detail}</p>}
    </Link>
  )
}

function GettingStarted() {
  return (
    <section>
      <h2 className={SECTION_TITLE}>Getting started</h2>
      <ol className="space-y-3">
        {NEXT_STEPS.map((step, index) => (
          <li key={step.title} className={`${CARD} flex gap-4 p-4`}>
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-100">
              {index + 1}
            </span>
            <div>
              <p className="text-sm font-medium">{step.title}</p>
              <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-4 flex gap-6">
        <Link to="/knowledge-bases" className={LINK}>
          Create a knowledge base →
        </Link>
        <Link to="/chat" className={LINK}>
          Start a chat →
        </Link>
      </div>
    </section>
  )
}

function processing(kb: KnowledgeBase): number {
  return (kb.status_counts.uploaded ?? 0) + (kb.status_counts.processing ?? 0)
}

export default function DashboardPage() {
  const { user } = useAuth()
  const firstName = user?.full_name?.split(' ')[0]
  const knowledgeBases = useResource(listKnowledgeBases)
  const conversations = useResource(listConversations)

  const kbs = knowledgeBases.data ?? []
  const chats = conversations.data ?? []
  const documents = kbs.reduce((sum, kb) => sum + kb.document_count, 0)
  const indexed = kbs.reduce((sum, kb) => sum + (kb.status_counts.completed ?? 0), 0)
  const loading = knowledgeBases.loading && !knowledgeBases.data
  const isNew = !loading && kbs.length === 0 && chats.length === 0

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{firstName ? `Welcome, ${firstName}` : 'Welcome'}</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {isNew ? 'Set up your first knowledge base to start asking questions.' : "Here's an overview of your workspace."}
          </p>
        </div>
        {!isNew && !loading && (
          <Link
            to="/chat"
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
          >
            New chat
          </Link>
        )}
      </header>

      <div className="mt-8 grid gap-8 lg:grid-cols-3">
        <div className="space-y-8 lg:col-span-2">
          {loading ? (
            <div className="h-28 animate-pulse rounded-xl bg-slate-200/60 dark:bg-slate-800/60" aria-label="Loading your workspace" />
          ) : isNew ? (
            <GettingStarted />
          ) : (
            <>
              <section aria-label="Workspace summary" className="grid gap-3 sm:grid-cols-3">
                <Stat label="Knowledge bases" value={kbs.length} to="/knowledge-bases" />
                <Stat
                  label="Documents"
                  value={documents}
                  detail={documents === indexed ? 'all indexed' : `${indexed} indexed`}
                  to="/knowledge-bases"
                />
                <Stat label="Conversations" value={chats.length} to="/chat" />
              </section>

              <section aria-labelledby="recent-heading">
                <h2 id="recent-heading" className={SECTION_TITLE}>
                  Recent conversations
                </h2>
                {chats.length === 0 ? (
                  <p className={`${CARD} px-4 py-6 text-sm text-slate-500 dark:text-slate-400`}>
                    No conversations yet. <Link to="/chat" className={LINK}>Ask your first question →</Link>
                  </p>
                ) : (
                  <ul className={`${CARD} divide-y divide-slate-100 dark:divide-slate-800`}>
                    {chats.slice(0, 5).map((chat) => (
                      <li key={chat.id}>
                        <Link to={`/chat/${chat.id}`} className="block px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                          <p className="truncate text-sm font-medium">{chat.title}</p>
                          <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                            {chat.knowledge_base_name ?? 'General chat'} · {pluralize(chat.message_count, 'message')} ·{' '}
                            {formatRelative(chat.updated_at)}
                          </p>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-labelledby="kbs-heading">
                <div className="mb-3 flex items-baseline justify-between">
                  <h2 id="kbs-heading" className={SECTION_TITLE.replace('mb-3 ', '')}>
                    Knowledge bases
                  </h2>
                  <Link to="/knowledge-bases" className={LINK}>
                    Manage →
                  </Link>
                </div>
                {kbs.length === 0 ? (
                  <p className={`${CARD} px-4 py-6 text-sm text-slate-500 dark:text-slate-400`}>
                    No knowledge bases yet. <Link to="/knowledge-bases" className={LINK}>Create one →</Link>
                  </p>
                ) : (
                  <ul className="grid gap-3 sm:grid-cols-2">
                    {kbs.slice(0, 6).map((kb) => (
                      <li key={kb.id}>
                        <Link to={`/knowledge-bases/${kb.id}`} className={`${CARD} block p-4 transition-colors hover:border-brand-500/50`}>
                          <p className="truncate text-sm font-medium">{kb.name}</p>
                          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                            {pluralize(kb.document_count, 'document')}
                            {processing(kb) > 0 && <span className="text-amber-700 dark:text-amber-400"> · {processing(kb)} processing</span>}
                          </p>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>

        <section>
          <h2 className={SECTION_TITLE}>System status</h2>
          <SystemStatusCard />
        </section>
      </div>
    </div>
  )
}
