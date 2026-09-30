import { useState } from 'react'

import type { DailyCount } from '../../services/dashboard'
import { pluralize } from '../../utils/format'

// Geometry in viewBox units; the SVG scales to the card width.
const WIDTH = 560
const HEIGHT = 120
const LABEL_SPACE = 16 // room above the tallest column for its value
const COLUMN = 18 // <= 24px at typical card widths, never filling the slot
const RADIUS = 4 // rounded data end, square at the baseline

function dayLabel(iso: string, style: 'short' | 'long' = 'short'): string {
  const date = new Date(`${iso}T00:00:00Z`)
  return date.toLocaleDateString('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    ...(style === 'long' ? { weekday: 'short' } : {}),
  })
}

/** A column path with a rounded top and a square base (grows up from the baseline). */
function columnPath(x: number, height: number): string {
  const r = Math.min(RADIUS, height, COLUMN / 2)
  const top = HEIGHT - height
  return `M${x},${HEIGHT} V${top + r} Q${x},${top} ${x + r},${top} H${x + COLUMN - r} Q${x + COLUMN},${top} ${x + COLUMN},${top + r} V${HEIGHT} Z`
}

/** Chat answers per UTC day. One series, so no legend: the card title names it. Mouse users get
 * a tooltip per column; the same numbers are in a table for screen readers. */
export function DailyAnswersChart({ days }: { days: DailyCount[] }) {
  const [hovered, setHovered] = useState<number | null>(null)
  const max = Math.max(1, ...days.map((day) => day.count))
  const slot = WIDTH / days.length
  const heightOf = (count: number) => (count ? Math.max(3, (count / max) * (HEIGHT - LABEL_SPACE)) : 0)
  // Label only the (first) peak; the tooltip and the table carry the rest.
  const peak = days.findIndex((day) => day.count === max && day.count > 0)

  return (
    <div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="block h-32 w-full overflow-visible"
          aria-hidden
          onMouseLeave={() => setHovered(null)}
        >
          <line x1={0} x2={WIDTH} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} className="stroke-slate-200 dark:stroke-slate-700" strokeWidth={1} />
          {days.map((day, index) => {
            const x = index * slot + (slot - COLUMN) / 2
            const height = heightOf(day.count)
            return (
              <g key={day.date} onMouseEnter={() => setHovered(index)}>
                {/* Hit target: the whole slot, taller than the mark. */}
                <rect x={index * slot} y={0} width={slot} height={HEIGHT} fill="transparent" />
                {height > 0 && (
                  <path
                    d={columnPath(x, height)}
                    className={`fill-brand-600 dark:fill-chart-dark ${hovered !== null && hovered !== index ? 'opacity-45' : ''}`}
                  />
                )}
              </g>
            )
          })}
        </svg>
        {/* HTML, not SVG text: the SVG stretches to the card width, which would distort text. */}
        {peak >= 0 && (
          <span
            aria-hidden
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-full pb-1 text-[11px] text-slate-600 tabular-nums dark:text-slate-300"
            style={{ left: `${((peak + 0.5) / days.length) * 100}%`, top: `${((HEIGHT - heightOf(days[peak].count)) / HEIGHT) * 100}%` }}
          >
            {days[peak].count}
          </span>
        )}
        {hovered !== null && (
          <div
            className="pointer-events-none absolute -top-9 z-10 -translate-x-1/2 rounded-md bg-slate-900 px-2 py-1 text-xs whitespace-nowrap text-white shadow dark:bg-slate-700"
            style={{ left: `${((hovered + 0.5) / days.length) * 100}%` }}
          >
            {dayLabel(days[hovered].date, 'long')} · {pluralize(days[hovered].count, 'answer')}
          </div>
        )}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-slate-500 tabular-nums dark:text-slate-400">
        <span>{dayLabel(days[0].date)}</span>
        <span>Today</span>
      </div>
      <table className="sr-only">
        <caption>Chat answers per day, last {days.length} days</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Answers</th>
          </tr>
        </thead>
        <tbody>
          {days.map((day) => (
            <tr key={day.date}>
              <td>{dayLabel(day.date, 'long')}</td>
              <td>{day.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
