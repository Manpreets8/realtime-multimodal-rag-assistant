import { useCallback, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { AnswerBody } from '../components/citations/AnswerBody'
import { SourceViewer, type ViewedSource } from '../components/citations/SourceViewer'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { useResource } from '../hooks/useResource'
import { ApiError } from '../services/api'
import { compareDocuments, type ComparedDocument, type CompareResponse, type TextUnit } from '../services/compare'
import { listAllDocuments } from '../services/documents'
import { pluralize } from '../utils/format'

const fetchDocuments = () => listAllDocuments({ status: 'completed', limit: 200, offset: 0 })

const selectClass =
  'block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900'

function where(unit: TextUnit): string {
  return unit.page_number ? `Page ${unit.page_number}` : unit.section ?? ''
}

function percent(share: number): string {
  return `${Math.round(share * 100)}%`
}

function UnitLine({ unit, onOpen, label }: { unit: TextUnit; onOpen: () => void; label: string }) {
  return (
    <li className="flex items-start justify-between gap-3 py-1.5 text-sm">
      <span className="min-w-0">
        {unit.text}
        {where(unit) && <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">{where(unit)}</span>}
      </span>
      <button
        type="button"
        onClick={onOpen}
        aria-label={label}
        className="shrink-0 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
      >
        Open
      </button>
    </li>
  )
}

function DiffList({ title, count, shown, children }: { title: string; count: number; shown: number; children: ReactNode }) {
  return (
    <details className="rounded-lg border border-slate-200 px-4 py-2 dark:border-slate-800" open={count > 0 && count <= 10}>
      <summary className="cursor-pointer text-sm font-medium">
        {title} <span className="text-slate-500 dark:text-slate-400">({count})</span>
      </summary>
      {count === 0 ? (
        <p className="py-2 text-sm text-slate-500 dark:text-slate-400">None.</p>
      ) : (
        <>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">{children}</ul>
          {shown < count && <p className="py-2 text-xs text-slate-500 dark:text-slate-400">Showing the first {shown} of {count}.</p>}
        </>
      )}
    </details>
  )
}

/** Compare two indexed documents: exact sentence differences and a cited AI analysis. */
export default function ComparePage() {
  const [params, setParams] = useSearchParams()
  const [documentA, setDocumentA] = useState(params.get('a') ?? '')
  const [documentB, setDocumentB] = useState(params.get('b') ?? '')
  const [result, setResult] = useState<CompareResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [comparing, setComparing] = useState(false)
  const [viewing, setViewing] = useState<ViewedSource | null>(null)
  const closeViewer = useCallback(() => setViewing(null), [])
  const { data: documents, loading } = useResource(fetchDocuments)
  const ready = documents?.items ?? []

  // Comparing calls the AI model, so it only runs on request, never on page load.
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!documentA || !documentB || documentA === documentB) return
    setParams({ a: documentA, b: documentB }, { replace: true })
    setComparing(true)
    setError(null)
    try {
      setResult(await compareDocuments(documentA, documentB))
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'The comparison failed.', null))
    } finally {
      setComparing(false)
    }
  }

  const open = (document: ComparedDocument, unit: TextUnit) =>
    setViewing({
      chunkId: unit.chunk_id,
      documentId: document.id,
      filename: document.filename,
      pageNumber: unit.page_number,
      section: unit.section,
    })

  const option = (id: string) => ready.find((d) => d.id === id)
  const analysis = result?.analysis
  const uncited = result && analysis ? [result.document_a, result.document_b].filter((d) => !analysis.cited_documents.includes(d.id)) : []
  const partial = result ? [result.document_a, result.document_b].filter((d) => d.coverage !== null && d.coverage < 1) : []

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8 sm:py-10">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        <Link to="/documents" className="hover:underline">
          Documents
        </Link>{' '}
        / Compare
      </p>
      <header className="mt-1">
        <h1 className="text-2xl font-semibold tracking-tight">Compare documents</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          See what was added, removed and changed between two documents, such as two versions of a resume or policy. Every
          difference links to the text it comes from.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="mt-6 grid gap-3 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-end">
        <div>
          <label htmlFor="compare-a" className="text-sm font-medium">
            Original (A)
          </label>
          <select id="compare-a" value={documentA} onChange={(e) => setDocumentA(e.target.value)} className={selectClass}>
            <option value="">Choose a document…</option>
            {ready.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename} — {d.knowledge_base_name}
              </option>
            ))}
          </select>
        </div>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            setDocumentA(documentB)
            setDocumentB(documentA)
          }}
          disabled={!documentA && !documentB}
          aria-label="Swap documents"
        >
          ⇄
        </Button>
        <div>
          <label htmlFor="compare-b" className="text-sm font-medium">
            Revised (B)
          </label>
          <select id="compare-b" value={documentB} onChange={(e) => setDocumentB(e.target.value)} className={selectClass}>
            <option value="">Choose a document…</option>
            {ready.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename} — {d.knowledge_base_name}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" loading={comparing} disabled={!documentA || !documentB || documentA === documentB}>
          Compare
        </Button>
      </form>
      {documentA && documentA === documentB && (
        <p className="mt-2 text-sm text-amber-800 dark:text-amber-300" role="alert">
          Choose two different documents.
        </p>
      )}
      {!loading && ready.length < 2 && (
        <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
          You need at least two indexed documents to compare. Upload them from a{' '}
          <Link to="/knowledge-bases" className="font-medium text-brand-600 hover:underline dark:text-brand-300">
            knowledge base
          </Link>
          .
        </p>
      )}
      {documents && [params.get('a'), params.get('b')].some((id) => id && !option(id)) && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">A document from the link is no longer available; choose another.</p>
      )}
      {comparing && <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">Comparing… the AI analysis can take up to a minute.</p>}

      {error && (
        <div className="mt-6">
          <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>
        </div>
      )}

      {result && (
        <div className="mt-8 space-y-8">
          <section aria-labelledby="overview-heading">
            <h2 id="overview-heading" className="text-lg font-semibold">
              {result.document_a.filename} → {result.document_b.filename}
            </h2>
            <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5" data-testid="comparison-counts">
              {[
                ['Identical text', percent(result.differences.overlap)],
                ['Added', String(result.differences.counts.added)],
                ['Removed', String(result.differences.counts.removed)],
                ['Modified', String(result.differences.counts.modified)],
                ['Unchanged', String(result.differences.counts.common)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
                  <dt className="text-xs text-slate-600 dark:text-slate-400">{label}</dt>
                  <dd className="text-xl font-semibold">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              Counted in sentences: {pluralize(result.document_a.sentences, 'sentence')} in A,{' '}
              {pluralize(result.document_b.sentences, 'sentence')} in B.
            </p>
          </section>

          <section aria-labelledby="analysis-heading" className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
            <h2 id="analysis-heading" className="text-lg font-semibold">
              AI analysis
            </h2>
            {analysis ? (
              <div className="mt-3 space-y-3">
                {partial.map((d) => (
                  <p key={d.id} className="text-xs text-amber-800 dark:text-amber-300" data-testid="partial-coverage">
                    {d.filename} is long: the AI read {percent(d.coverage!)} of it, starting with the passages that changed.
                    The exact differences below cover the whole document.
                  </p>
                ))}
                {uncited.map((d) => (
                  <p key={d.id} className="text-xs text-amber-800 dark:text-amber-300" data-testid="uncited-document">
                    The analysis doesn&apos;t cite {d.filename}; check its claims against the exact differences below.
                  </p>
                ))}
                <AnswerBody
                  text={analysis.text}
                  answerType={null}
                  citations={analysis.citations}
                  sources={analysis.sources}
                  truncated={analysis.truncated}
                  model={analysis.model}
                  usage={analysis.usage}
                  timingsMs={result.timings_ms}
                />
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-400" data-testid="analysis-unavailable">
                {result.analysis_unavailable}
              </p>
            )}
          </section>

          <section aria-labelledby="exact-heading" className="space-y-3">
            <div>
              <h2 id="exact-heading" className="text-lg font-semibold">
                Exact text differences
              </h2>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Sentence by sentence, computed without AI. A sentence counts as modified when most of its text is the same.
              </p>
            </div>
            <DiffList title="Added in B" count={result.differences.counts.added} shown={result.differences.added.length}>
              {result.differences.added.map((unit, i) => (
                <UnitLine key={i} unit={unit} onOpen={() => open(result.document_b, unit)} label={`Open added sentence ${i + 1} in ${result.document_b.filename}`} />
              ))}
            </DiffList>
            <DiffList title="Removed from A" count={result.differences.counts.removed} shown={result.differences.removed.length}>
              {result.differences.removed.map((unit, i) => (
                <UnitLine key={i} unit={unit} onOpen={() => open(result.document_a, unit)} label={`Open removed sentence ${i + 1} in ${result.document_a.filename}`} />
              ))}
            </DiffList>
            <DiffList title="Modified" count={result.differences.counts.modified} shown={result.differences.modified.length}>
              {result.differences.modified.map((pair, i) => (
                <li key={i} className="space-y-1 py-2 text-sm">
                  <p className="flex items-start justify-between gap-3">
                    <span>
                      <span className="mr-1 rounded bg-red-50 px-1 text-xs font-medium text-red-800 dark:bg-red-500/15 dark:text-red-200">A</span>
                      <del className="text-slate-600 dark:text-slate-400">{pair.before.text}</del>
                    </span>
                    <button type="button" onClick={() => open(result.document_a, pair.before)} aria-label={`Open modified sentence ${i + 1} in ${result.document_a.filename}`} className="shrink-0 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300">
                      Open
                    </button>
                  </p>
                  <p className="flex items-start justify-between gap-3">
                    <span>
                      <span className="mr-1 rounded bg-emerald-50 px-1 text-xs font-medium text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200">B</span>
                      <ins className="no-underline">{pair.after.text}</ins>
                    </span>
                    <button type="button" onClick={() => open(result.document_b, pair.after)} aria-label={`Open modified sentence ${i + 1} in ${result.document_b.filename}`} className="shrink-0 text-xs font-medium text-brand-600 hover:underline dark:text-brand-300">
                      Open
                    </button>
                  </p>
                </li>
              ))}
            </DiffList>
            <DiffList title="Unchanged" count={result.differences.counts.common} shown={result.differences.common.length}>
              {result.differences.common.map((unit, i) => (
                <UnitLine key={i} unit={unit} onOpen={() => open(result.document_b, unit)} label={`Open unchanged sentence ${i + 1} in ${result.document_b.filename}`} />
              ))}
            </DiffList>
          </section>
        </div>
      )}

      {viewing && <SourceViewer source={viewing} onClose={closeViewer} />}
    </div>
  )
}
