const BYTE_UNITS = ['B', 'KB', 'MB', 'GB']

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—'
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1
  return `${value.toFixed(digits)} ${BYTE_UNITS[unit]}`
}

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : dateFormatter.format(date)
}

const relativeFormatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const RELATIVE_STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 30],
  ['month', 12],
  ['year', Infinity],
]

export function formatRelative(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  let value = (date.getTime() - now.getTime()) / 1000
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(value) < size) return relativeFormatter.format(Math.round(value), unit)
    value /= size
  }
  return formatDateTime(iso)
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}
