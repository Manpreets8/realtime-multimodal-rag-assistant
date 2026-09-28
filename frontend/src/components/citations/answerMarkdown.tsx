import { Fragment, type ReactNode } from 'react'

/**
 * A small markdown renderer for answers: paragraphs, bullet and numbered lists, headings,
 * fenced code, **bold**, *italic* and `code`. Anything else stays literal text.
 *
 * Citations point at character ranges of the raw answer, so everything here keeps raw
 * offsets: blocks know the range of their content, inline runs know the offsets of their
 * visible characters, and runs are cut wherever a cited span starts or ends. That keeps each
 * citation marker exactly after the text it supports, even inside a list item or bold text.
 * Only React elements are produced (no HTML strings), so answer text can't inject markup.
 */

export interface CitedSpan {
  start: number
  end: number
  key: string
}

interface Run {
  start: number // raw offset of the first visible character
  end: number // raw offset after the last visible character
  text: string
  bold?: boolean
  italic?: boolean
  code?: boolean
}

type Block =
  | { kind: 'paragraph' | 'heading'; lines: [number, number][] }
  | { kind: 'list'; ordered: boolean; items: [number, number][] }
  | { kind: 'code'; range: [number, number] }

const BULLET = /^(\s*)([-*+•]|\d+[.)])\s+/
const HEADING = /^\s{0,3}#{1,6}\s+/
const FENCE = /^\s*```/

/** Split the answer into blocks, keeping the raw offsets of each block's content. */
export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = []
  const lines: { start: number; end: number; text: string }[] = []
  let offset = 0
  for (const line of text.split('\n')) {
    lines.push({ start: offset, end: offset + line.length, text: line })
    offset += line.length + 1
  }

  let paragraph: [number, number][] | null = null
  let list: Extract<Block, { kind: 'list' }> | null = null
  const flush = () => {
    if (paragraph) blocks.push({ kind: 'paragraph', lines: paragraph })
    if (list) blocks.push(list)
    paragraph = null
    list = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (FENCE.test(line.text)) {
      flush()
      const close = lines.findIndex((l, j) => j > i && FENCE.test(l.text))
      const last = close === -1 ? lines.length - 1 : close
      const bodyStart = i + 1 <= last ? lines[i + 1]?.start ?? line.end : line.end
      const bodyEnd = close === -1 ? lines[last].end : lines[close].start - 1
      blocks.push({ kind: 'code', range: [bodyStart, Math.max(bodyStart, bodyEnd)] })
      i = last
      continue
    }
    if (!line.text.trim()) {
      flush()
      continue
    }
    const heading = HEADING.exec(line.text)
    if (heading) {
      flush()
      blocks.push({ kind: 'heading', lines: [[line.start + heading[0].length, line.end]] })
      continue
    }
    const bullet = BULLET.exec(line.text)
    if (bullet) {
      const ordered = /\d/.test(bullet[2])
      if (paragraph || (list && list.ordered !== ordered)) flush()
      list ??= { kind: 'list', ordered, items: [] }
      list.items.push([line.start + bullet[0].length, line.end])
      continue
    }
    if (list && /^\s+\S/.test(line.text)) {
      // An indented continuation line belongs to the previous list item.
      const last = list.items[list.items.length - 1]
      last[1] = line.end
      continue
    }
    if (list) flush()
    paragraph ??= []
    paragraph.push([line.start, line.end])
  }
  flush()
  return blocks
}

const INLINE = /`([^`\n]+)`|\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*|__(?=\S)([^_\n]+?)(?<=\S)__|\*(?=\S)([^*\n]+?)(?<=\S)\*|(?<![\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w])/g

/** Inline markdown in text[start, end) as runs of visible text with raw offsets. */
export function parseInline(text: string, start: number, end: number): Run[] {
  const source = text.slice(start, end)
  const runs: Run[] = []
  let cursor = 0
  for (const match of source.matchAll(INLINE)) {
    const at = match.index
    if (at > cursor) runs.push({ start: start + cursor, end: start + at, text: source.slice(cursor, at) })
    const [code, bold1, bold2, italic1, italic2] = match.slice(1)
    const content = code ?? bold1 ?? bold2 ?? italic1 ?? italic2
    const marker = code !== undefined ? 1 : bold1 !== undefined || bold2 !== undefined ? 2 : 1
    const contentStart = start + at + marker
    runs.push({
      start: contentStart,
      end: contentStart + content.length,
      text: content,
      code: code !== undefined,
      bold: bold1 !== undefined || bold2 !== undefined,
      italic: italic1 !== undefined || italic2 !== undefined,
    })
    cursor = at + match[0].length
  }
  if (cursor < source.length) runs.push({ start: start + cursor, end, text: source.slice(cursor) })
  return runs
}

/** Cut runs at every citation boundary, so a piece is either wholly cited or not. */
function splitRuns(runs: Run[], spans: CitedSpan[]): Run[] {
  const cuts = [...new Set(spans.flatMap((s) => [s.start, s.end]))].sort((a, b) => a - b)
  const pieces: Run[] = []
  for (const run of runs) {
    let from = run.start
    for (const cut of cuts) {
      if (cut > from && cut < run.end) {
        pieces.push({ ...run, start: from, end: cut, text: run.text.slice(from - run.start, cut - run.start) })
        from = cut
      }
    }
    pieces.push({ ...run, start: from, end: run.end, text: run.text.slice(from - run.start) })
  }
  return pieces.filter((piece) => piece.text)
}

function styled(piece: Run): ReactNode {
  let node: ReactNode = piece.text
  if (piece.code) {
    node = <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.8em] dark:bg-slate-800">{node}</code>
  }
  if (piece.italic) node = <em>{node}</em>
  if (piece.bold) node = <strong className="font-semibold">{node}</strong>
  return node
}

interface RenderOptions {
  spans: CitedSpan[]
  marker: (span: CitedSpan) => ReactNode
}

/** Inline content of one line or list item, with cited text underlined and markers placed. */
function renderInline(text: string, [start, end]: [number, number], options: RenderOptions, placed: Set<string>): ReactNode[] {
  const nodes: ReactNode[] = []
  const pending = (upTo: number) =>
    options.spans
      .filter((span) => !placed.has(span.key) && span.end <= upTo && span.end > span.start)
      .sort((a, b) => a.end - b.end)
  const emitMarkers = (upTo: number) => {
    for (const span of pending(upTo)) {
      placed.add(span.key)
      nodes.push(<Fragment key={`marker-${span.key}`}>{options.marker(span)}</Fragment>)
    }
  }

  for (const piece of splitRuns(parseInline(text, start, end), options.spans)) {
    emitMarkers(piece.start)
    const cited = options.spans.some((span) => span.start <= piece.start && piece.end <= span.end)
    const content = styled(piece)
    nodes.push(
      cited ? (
        <span key={`c${piece.start}`} className="decoration-brand-500/40 decoration-dotted underline-offset-4 hover:underline">
          {content}
        </span>
      ) : (
        <span key={`t${piece.start}`}>{content}</span>
      ),
    )
  }
  // Markers for spans that end with this line, or in the whitespace after it (usually right
  // after the final full stop), belong here rather than at the start of the next block.
  let upTo = end
  while (upTo < text.length && /\s/.test(text[upTo])) upTo++
  emitMarkers(upTo)
  return nodes
}

/** The whole answer as blocks of formatted, cited text. */
export function renderAnswer(text: string, options: RenderOptions): ReactNode[] {
  const placed = new Set<string>()
  const output = parseBlocks(text).map((block, index) => {
    if (block.kind === 'code') {
      return (
        <pre key={index} className="overflow-x-auto rounded-lg bg-slate-100 p-3 font-mono text-xs dark:bg-slate-800">
          {text.slice(...block.range)}
        </pre>
      )
    }
    if (block.kind === 'list') {
      const List = block.ordered ? 'ol' : 'ul'
      return (
        <List key={index} className={`space-y-1 pl-5 ${block.ordered ? 'list-decimal' : 'list-disc'}`}>
          {block.items.map((item) => (
            <li key={item[0]}>{renderInline(text, item, options, placed)}</li>
          ))}
        </List>
      )
    }
    const lines = block.lines.flatMap((line, i) => [
      ...(i > 0 ? [<br key={`br${line[0]}`} />] : []),
      ...renderInline(text, line, options, placed),
    ])
    return block.kind === 'heading' ? (
      <p key={index} className="font-semibold">
        {lines}
      </p>
    ) : (
      <p key={index}>{lines}</p>
    )
  })
  // Defensive: a span whose end falls outside every block (e.g. in trailing blank lines).
  const leftover = options.spans.filter((span) => !placed.has(span.key))
  if (leftover.length) output.push(<p key="leftover">{leftover.map((span) => <Fragment key={span.key}>{options.marker(span)}</Fragment>)}</p>)
  return output
}
