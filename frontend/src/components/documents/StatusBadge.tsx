import type { DocumentStatus } from '../../services/documents'

const STYLES: Record<DocumentStatus, { label: string; className: string; dot: string }> = {
  uploaded: {
    label: 'Queued',
    className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    dot: 'bg-slate-400',
  },
  processing: {
    label: 'Processing',
    className: 'bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300',
    dot: 'bg-amber-500 animate-pulse',
  },
  completed: {
    label: 'Indexed',
    className: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300',
    dot: 'bg-emerald-500',
  },
  failed: {
    label: 'Failed',
    className: 'bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300',
    dot: 'bg-rose-500',
  },
}

export function StatusBadge({ status, title }: { status: DocumentStatus; title?: string }) {
  const style = STYLES[status]
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}
    >
      <span aria-hidden className={`size-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  )
}
