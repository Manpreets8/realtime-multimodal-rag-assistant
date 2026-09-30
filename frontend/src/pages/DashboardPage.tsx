import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { ActivityFeed } from '../components/dashboard/ActivityFeed'
import { DailyAnswersChart } from '../components/dashboard/DailyAnswersChart'
import { SystemStatusCard } from '../components/SystemStatusCard'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Skeleton } from '../components/ui/Skeleton'
import { useAuth } from '../hooks/useAuth'
import { useResource } from '../hooks/useResource'
import { listConversations } from '../services/chat'
import { getDashboard, type DashboardStats } from '../services/dashboard'
import { listKnowledgeBases, type KnowledgeBase } from '../services/knowledgeBases'
import { formatRelative, pluralize } from '../utils/format'

const NEXT_STEPS = [
  { title: 'Create a knowledge base', text: 'Group related documents, such as company policies or project docs.' },
  { title: 'Upload documents', text: 'Add PDF, Word, text and Markdown files to a knowledge base.' },
  { title: 'Ask questions', text: 'Chat with a knowledge base and get answers with source citations, by text or voice.' },
]

const SECTION_TITLE = 'text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400'
const CARD = 'rounded-xl border border-slate-200 bg-white shadow-xs dark:border-slate-800 dark:bg-slate-900'
const LINK = 'text-sm font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300'

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })

function StatTile({ label, value, detail, to }: {
  label: string
  value: number
  detail?: ReactNode
  to?: string
}) {
  const content = (
    <>
      <p className="text-sm text-slate-600 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-3xl font-semibold tracking-tight">{compact.format(value)}</p>
      {detail && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{detail}</p>}
    </>
  )
  return to ? (
    <Link to={to} className={`${CARD} block p-5 transition-colors hover:border-brand-500/50`}>
      {content}
    </Link>
  ) : (
    <div className={`${CARD} p-5`}>{content}</div>
  )
}

function StatTiles({ stats }: { stats: DashboardStats }) {
  const { documents, ai_answers: ai } = stats
  const documentDetail = [
    `${documents.indexed} indexed`,
    documents.processing ? `${documents.processing} processing` : null,
    documents.failed ? `${documents.failed} failed` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <section aria-label="Workspace summary" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile label="Knowledge bases" value={stats.knowledge_bases} to="/knowledge-bases" />
      <StatTile label="Documents" value={documents.total} detail={documentDetail} to="/documents" />
      <StatTile label="Conversations" value={stats.conversations} to="/chat" />
      <StatTile
        label={`AI answers · ${ai.days} days`}
        value={ai.answers}
        detail={`${compact.format(ai.input_tokens + ai.output_tokens)} tokens in chat`}
      />
    </section>
  )
}

function AnswersCard({ stats }: { stats: DashboardStats }) {
  const { ai_answers: ai } = stats
  const recent = ai.by_day.reduce((sum, day) => sum + day.count, 0)
  return (
    <section aria-labelledby="answers-heading" className={`${CARD} p-5`}>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="answers-heading" className={SECTION_TITLE}>
          Chat answers per day
        </h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {pluralize(recent, 'answer')} in the last {ai.by_day.length} days · input {compact.format(ai.input_tokens)},
          output {compact.format(ai.output_tokens)} tokens over {ai.days} days
        </p>
      </div>
      <DailyAnswersChart days={ai.by_day} />
    </section>
  )
}

function LoadingDashboard() {
  return (
    <div aria-busy="true" aria-label="Loading your workspace" className="space-y-8">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className={`${CARD} space-y-3 p-5`}>
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-3 w-32" />
          </div>
        ))}
      </div>
      <div className={`${CARD} space-y-4 p-5`}>
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="flex items-center gap-3">
            <Skeleton className="size-8 rounded-full" />
            <Skeleton className="h-4 flex-1" />
          </div>
        ))}
      </div>
    </div>
  )
}

function GettingStarted() {
  return (
    <section aria-labelledby="getting-started">
      <h2 id="getting-started" className={`${SECTION_TITLE} mb-3`}>
        Getting started
      </h2>
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

function greeting(now = new Date()): string {
  const hour = now.getHours()
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

export default function DashboardPage() {
  const { user } = useAuth()
  const firstName = user?.full_name?.split(' ')[0]
  const dashboard = useResource(getDashboard)
  const knowledgeBases = useResource(listKnowledgeBases)
  const conversations = useResource(listConversations)

  const stats = dashboard.data?.stats
  const kbs = knowledgeBases.data ?? []
  const chats = conversations.data ?? []
  const loading = dashboard.loading && !dashboard.data
  const isNew = stats !== undefined && stats.knowledge_bases === 0 && stats.conversations === 0

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-8 sm:py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {greeting()}
            {firstName ? `, ${firstName}` : ''}
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {isNew ? 'Set up your first knowledge base to start asking questions.' : "Here's what's happening in your workspace."}
          </p>
        </div>
        {stats && !isNew && (
          <div className="flex gap-2">
            <Link
              to="/knowledge-bases"
              className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Add documents
            </Link>
            <Link
              to="/chat"
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
            >
              New chat
            </Link>
          </div>
        )}
      </header>

      <div className="mt-8 space-y-8">
        {dashboard.error && !dashboard.data ? (
          <div className="space-y-3">
            <ErrorAlert requestId={dashboard.error.status >= 500 ? dashboard.error.requestId : null}>
              {dashboard.error.message}
            </ErrorAlert>
            <Button variant="secondary" onClick={dashboard.reload}>
              Try again
            </Button>
          </div>
        ) : loading ? (
          <LoadingDashboard />
        ) : stats && !isNew ? (
          <StatTiles stats={stats} />
        ) : null}

        <div className="grid gap-8 lg:grid-cols-3">
          <div className="space-y-8 lg:col-span-2">
            {isNew && <GettingStarted />}

            {stats && !isNew && <AnswersCard stats={stats} />}

            {stats && !isNew && (
              <section aria-labelledby="activity-heading" className={CARD}>
                <h2 id="activity-heading" className={`${SECTION_TITLE} border-b border-slate-200 px-5 py-4 dark:border-slate-800`}>
                  Recent activity
                </h2>
                <ActivityFeed items={dashboard.data?.activity ?? []} />
              </section>
            )}

            {stats && !isNew && (
              <div className="grid gap-8 md:grid-cols-2">
                <section aria-labelledby="recent-heading">
                  <div className="mb-3 flex items-baseline justify-between">
                    <h2 id="recent-heading" className={SECTION_TITLE}>
                      Recent conversations
                    </h2>
                    <Link to="/chat" className={LINK}>
                      All →
                    </Link>
                  </div>
                  {conversations.loading && !conversations.data ? (
                    <Skeleton className="h-32 w-full rounded-xl" />
                  ) : chats.length === 0 ? (
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
                    <h2 id="kbs-heading" className={SECTION_TITLE}>
                      Knowledge bases
                    </h2>
                    <Link to="/knowledge-bases" className={LINK}>
                      Manage →
                    </Link>
                  </div>
                  {knowledgeBases.loading && !knowledgeBases.data ? (
                    <Skeleton className="h-32 w-full rounded-xl" />
                  ) : kbs.length === 0 ? (
                    <p className={`${CARD} px-4 py-6 text-sm text-slate-500 dark:text-slate-400`}>
                      No knowledge bases yet. <Link to="/knowledge-bases" className={LINK}>Create one →</Link>
                    </p>
                  ) : (
                    <ul className={`${CARD} divide-y divide-slate-100 dark:divide-slate-800`}>
                      {kbs.slice(0, 5).map((kb) => (
                        <li key={kb.id}>
                          <Link to={`/knowledge-bases/${kb.id}`} className="block px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                            <p className="truncate text-sm font-medium">{kb.name}</p>
                            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                              {pluralize(kb.document_count, 'document')}
                              {processing(kb) > 0 && (
                                <span className="text-amber-700 dark:text-amber-400"> · {processing(kb)} processing</span>
                              )}
                            </p>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            )}
          </div>

          <section aria-labelledby="status-heading">
            <h2 id="status-heading" className={`${SECTION_TITLE} mb-3`}>
              System status
            </h2>
            <SystemStatusCard />
          </section>
        </div>
      </div>
    </div>
  )
}
