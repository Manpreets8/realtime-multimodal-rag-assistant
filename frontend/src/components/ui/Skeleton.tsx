/** A placeholder block shown while content loads. Purely visual: pair it with a status message
 * (e.g. `aria-busy` on the region) so screen readers know something is loading. */
export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`rounded-md bg-slate-200/70 motion-safe:animate-pulse dark:bg-slate-800 ${className}`}
    />
  )
}
