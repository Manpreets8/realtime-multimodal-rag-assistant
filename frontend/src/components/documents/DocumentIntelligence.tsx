import type { DocumentItem, IngestionProgress } from '../../services/documents'
import { failureAdvice } from '../../utils/documentFailures'
import { formatBytes } from '../../utils/format'

// --- pipeline stages ----------------------------------------------------------------------

const STEPS: { id: 'queued' | IngestionProgress['stage']; label: string }[] = [
  { id: 'queued', label: 'Queued' },
  { id: 'extracting', label: 'Extracting' },
  { id: 'chunking', label: 'Chunking' },
  { id: 'embedding', label: 'Embedding' },
  { id: 'indexing', label: 'Indexing' },
]

const STEP_DETAIL: Record<(typeof STEPS)[number]['id'], string> = {
  queued: 'Waiting for a worker',
  extracting: 'Reading text and metadata',
  chunking: 'Splitting into passages',
  embedding: 'Creating embeddings',
  indexing: 'Writing to the search index',
}

/** Where a queued or processing document is in the pipeline. Without live progress (e.g. Redis
 * unavailable) a processing document shows a general message without a step. */
export function PipelineSteps({ document }: { document: DocumentItem }) {
  const progress = document.progress ?? null
  const current = document.status === 'uploaded' ? 'queued' : progress?.stage
  const index = STEPS.findIndex((step) => step.id === current)
  const embedding = progress?.stage === 'embedding' && progress.total > 0
  const detail = current
    ? embedding
      ? `Embedding ${progress.done}/${progress.total} passages`
      : STEP_DETAIL[current]
    : 'Extracting and indexing…'

  return (
    <div className="mt-1 max-w-sm">
      <p className="truncate text-xs text-slate-600 dark:text-slate-400">
        {index >= 0 && <span className="sr-only">Step {index + 1} of {STEPS.length}: </span>}
        {detail}
      </p>
      {index >= 0 && (
        <ol className="mt-1.5 flex gap-1" aria-hidden>
          {STEPS.map((step, i) => (
            <li
              key={step.id}
              title={step.label}
              className={`h-1 flex-1 rounded-full ${
                i < index
                  ? 'bg-emerald-500'
                  : i === index
                    ? 'bg-amber-500 motion-safe:animate-pulse'
                    : 'bg-slate-200 dark:bg-slate-700'
              }`}
            />
          ))}
        </ol>
      )}
      {embedding && (
        <div
          role="progressbar"
          aria-label={`Embedding ${progress.done} of ${progress.total} passages`}
          aria-valuemin={0}
          aria-valuemax={progress.total}
          aria-valuenow={progress.done}
          className="mt-1 h-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
        >
          <div
            className="h-full rounded-full bg-amber-500 transition-[width] duration-500"
            style={{ width: `${(100 * progress.done) / progress.total}%` }}
          />
        </div>
      )}
    </div>
  )
}

// --- details panel ----------------------------------------------------------------------------

function duration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
}

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function Field({ label, value, className }: { label: string; value: string | number | null | undefined; className?: string }) {
  // A dt/dd pair in one div: the only wrapper a <dl> allows around its items.
  return (
    <div className={className}>
      <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm break-words">{value === null || value === undefined || value === '' ? '—' : value}</dd>
    </div>
  )
}

export function DocumentInsights({ document }: { document: DocumentItem }) {
  const metadata = document.extracted_metadata
  const stats = document.processing_stats
  const advice = document.status === 'failed' ? failureAdvice(document) : null

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section aria-label="Document properties">
        <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">Properties</h3>
        <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Field label="Title" value={metadata?.title} />
          <Field label="Author" value={metadata?.author} />
          <Field label="Document date" value={metadata?.document_date ? formatDay(metadata.document_date) : null} />
          <Field label="Type" value={`${document.extension.slice(1).toUpperCase()} · ${formatBytes(document.size_bytes)}`} />
          <Field label="Pages" value={document.page_count} />
          <Field label="Words" value={metadata ? metadata.word_count.toLocaleString('en') : null} />
          <Field label="Sections" value={metadata ? metadata.section_count : null} />
          <Field label="Tables" value={metadata ? metadata.table_count : null} />
          <Field label="Passages" value={document.status === 'completed' ? document.chunk_count : null} />
        </dl>
        {!metadata && document.status === 'completed' && (
          <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
            Processed before properties were recorded. Re-process it to extract them.
          </p>
        )}
      </section>

      <section aria-label="Processing">
        <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">Processing</h3>
        {document.status === 'failed' ? (
          <div className="mt-2 space-y-1 text-sm">
            <p className="text-rose-700 dark:text-rose-300">{document.error_message ?? 'Processing failed.'}</p>
            {advice && <p className="text-slate-600 dark:text-slate-400">{advice}</p>}
          </div>
        ) : stats ? (
          <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Extract" value={duration(stats.extraction_ms)} />
            <Field label="Chunk" value={duration(stats.chunking_ms)} />
            <Field label="Embed" value={duration(stats.embedding_ms)} />
            <Field label="Index" value={duration(stats.indexing_ms)} />
            <Field label="Total" value={duration(stats.total_ms)} />
            <Field label="Characters" value={stats.characters.toLocaleString('en')} />
            <Field label="Embedding model" value={stats.embedding_model} className="col-span-full" />
          </dl>
        ) : (
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
            {document.status === 'completed' ? 'No timings recorded for this run.' : 'Not processed yet.'}
          </p>
        )}
      </section>
    </div>
  )
}
