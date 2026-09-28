import type { ReactNode } from 'react'

export type Range = [start: number, end: number]

/** Sort, clip to the text, and merge overlapping or touching ranges. */
export function normalizeRanges(ranges: Range[], length: number): Range[] {
  const clipped = ranges
    .map(([start, end]): Range => [Math.max(0, start), Math.min(length, end)])
    .filter(([start, end]) => end > start)
    .sort((a, b) => a[0] - b[0])
  const merged: Range[] = []
  for (const range of clipped) {
    const last = merged.at(-1)
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1])
    else merged.push([...range])
  }
  return merged
}

/** Render `text` with the given character ranges wrapped in <mark>. No HTML is ever injected. */
export function highlightRanges(text: string, ranges: Range[], className = ''): ReactNode[] {
  const nodes: ReactNode[] = []
  let cursor = 0
  for (const [start, end] of normalizeRanges(ranges, text.length)) {
    if (start > cursor) nodes.push(text.slice(cursor, start))
    nodes.push(
      <mark key={start} className={`rounded bg-amber-100 px-0.5 text-inherit dark:bg-amber-500/25 ${className}`}>
        {text.slice(start, end)}
      </mark>,
    )
    cursor = end
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}
