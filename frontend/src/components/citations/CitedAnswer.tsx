import type { AnswerCitation } from '../../services/rag'
import { renderAnswer, type CitedSpan } from './answerMarkdown'

interface CitedAnswerProps {
  text: string
  citations: Pick<AnswerCitation, 'source_number' | 'answer_spans'>[]
  /** Short description of a source, used in marker tooltips and accessible names. */
  describe: (sourceNumber: number) => string
  onSelect: (sourceNumber: number) => void
}

interface Segment {
  start: number
  end: number
  sources: number[]
}

/** Group citations by the answer span they support; spans are ordered and non-overlapping. */
function segments(text: string, citations: CitedAnswerProps['citations']): Segment[] {
  const bySpan = new Map<string, Segment>()
  for (const citation of citations) {
    for (const [start, end] of citation.answer_spans) {
      const key = `${start}:${end}`
      const segment = bySpan.get(key) ?? { start, end, sources: [] }
      if (!segment.sources.includes(citation.source_number)) segment.sources.push(citation.source_number)
      bySpan.set(key, segment)
    }
  }
  return [...bySpan.values()]
    .filter((segment) => segment.start < segment.end && segment.end <= text.length)
    .map((segment) => ({ ...segment, sources: segment.sources.sort((a, b) => a - b) }))
    .sort((a, b) => a.start - b.start || a.end - b.end)
}

/**
 * The answer, formatted (paragraphs, lists, bold, code) with a clickable citation marker after
 * each span a source supports. Markers are real buttons: keyboard reachable, with an accessible
 * name naming the source.
 */
export function CitedAnswer({ text, citations, describe, onSelect }: CitedAnswerProps) {
  const bySpan = new Map(segments(text, citations).map((segment) => [`${segment.start}:${segment.end}`, segment]))
  const marker = (span: CitedSpan) => (
    <sup className="ml-0.5 whitespace-nowrap">
      {bySpan.get(span.key)!.sources.map((number) => (
        <button
          key={number}
          type="button"
          onClick={() => onSelect(number)}
          title={describe(number)}
          aria-label={`Source ${number}: ${describe(number)}`}
          // <sup> has line-height: 0 (Tailwind preflight); without its own line height the
          // button collapses to zero height and becomes unclickable.
          className="mx-px inline-block rounded bg-brand-50 px-1 py-0.5 text-[0.7rem] leading-none font-semibold text-brand-700 hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-600 dark:bg-brand-500/15 dark:text-brand-100"
        >
          {number}
        </button>
      ))}
    </sup>
  )
  const spans: CitedSpan[] = [...bySpan.entries()].map(([key, segment]) => ({ key, start: segment.start, end: segment.end }))
  return <div className="space-y-2 text-sm leading-relaxed">{renderAnswer(text, { spans, marker })}</div>
}
