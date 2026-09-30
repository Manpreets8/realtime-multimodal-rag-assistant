import { Link } from 'react-router'

import type { ActivityItem, ActivityKind } from '../../services/dashboard'
import { formatRelative } from '../../utils/format'

const KINDS: Record<ActivityKind, { verb: string; icon: string; tone: string }> = {
  knowledge_base_created: {
    verb: 'Created knowledge base',
    icon: 'M4 6h16M4 12h16M4 18h10',
    tone: 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-100',
  },
  document_uploaded: {
    verb: 'Uploaded',
    icon: 'M12 16V4m0 0l-4 4m4-4l4 4M4 20h16',
    tone: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  },
  document_ready: {
    verb: 'Ready to search',
    icon: 'M5 12l5 5L20 7',
    tone: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  },
  document_failed: {
    verb: 'Processing failed',
    icon: 'M12 8v5m0 3h.01M12 21a9 9 0 100-18 9 9 0 000 18z',
    tone: 'bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-300',
  },
  conversation_started: {
    verb: 'Started a conversation',
    icon: 'M4 5h16v11H8l-4 4z',
    tone: 'bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300',
  },
}

function target(item: ActivityItem): string | null {
  if (item.conversation_id) return `/chat/${item.conversation_id}`
  if (item.knowledge_base_id) return `/knowledge-bases/${item.knowledge_base_id}`
  return null
}

export function ActivityFeed({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) {
    return (
      <p className="px-5 py-8 text-center text-sm text-slate-500 dark:text-slate-400">
        Nothing yet. Uploads, processed documents and new conversations will appear here.
      </p>
    )
  }
  return (
    <ol className="divide-y divide-slate-100 dark:divide-slate-800">
      {items.map((item, index) => {
        const kind = KINDS[item.kind]
        const href = target(item)
        const body = (
          <>
            <span className={`flex size-8 shrink-0 items-center justify-center rounded-full ${kind.tone}`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="size-4" aria-hidden>
                <path d={kind.icon} />
              </svg>
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">
                <span className="text-slate-500 dark:text-slate-400">{kind.verb}</span>{' '}
                <span className="font-medium">{item.title}</span>
              </p>
              {item.detail && <p className="truncate text-xs text-slate-500 dark:text-slate-400">{item.detail}</p>}
            </div>
            <time dateTime={item.at} className="shrink-0 text-xs text-slate-500 dark:text-slate-400">
              {formatRelative(item.at)}
            </time>
          </>
        )
        return (
          <li key={`${item.kind}-${item.at}-${index}`}>
            {href ? (
              <Link to={href} className="flex items-center gap-3 px-5 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                {body}
              </Link>
            ) : (
              <div className="flex items-center gap-3 px-5 py-3">{body}</div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
