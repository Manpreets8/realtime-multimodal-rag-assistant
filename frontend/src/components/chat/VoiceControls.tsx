import type { RecorderStatus } from '../../hooks/useVoiceRecorder'

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export function MicButton({ status, disabled, onStart }: { status: RecorderStatus; disabled?: boolean; onStart: () => void }) {
  const busy = status !== 'idle'
  return (
    <button
      type="button"
      onClick={onStart}
      disabled={disabled || busy}
      aria-label="Record a voice message"
      title="Record a voice message"
      className="flex size-[42px] shrink-0 items-center justify-center rounded-xl border border-slate-300 text-slate-500 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800 dark:text-slate-400"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0014 0M12 18v3" strokeLinecap="round" />
      </svg>
    </button>
  )
}

/** Shown above the composer while recording or transcribing. */
export function RecordingBar({
  status,
  elapsed,
  maxSeconds,
  onStop,
  onCancel,
}: {
  status: RecorderStatus
  elapsed: number
  maxSeconds: number
  onStop: () => void
  onCancel: () => void
}) {
  if (status === 'idle') return null
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800/60" role="status" aria-live="polite">
      {status === 'recording' ? (
        <>
          <span className="size-2.5 animate-pulse rounded-full bg-rose-500" aria-hidden />
          <span className="font-medium">Recording</span>
          <span className="font-mono text-slate-500 tabular-nums dark:text-slate-400" aria-label={`${elapsed} seconds`}>
            {clock(elapsed)} / {clock(maxSeconds)}
          </span>
          <span className="flex-1" />
          <button type="button" onClick={onCancel} className="rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-700">
            Cancel
          </button>
          <button type="button" onClick={onStop} className="rounded-md bg-brand-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-brand-700">
            Stop & transcribe
          </button>
        </>
      ) : status === 'requesting' ? (
        <span className="text-slate-500 dark:text-slate-400">Waiting for microphone permission…</span>
      ) : (
        <>
          <span className="size-4 animate-spin rounded-full border-2 border-brand-600 border-r-transparent" aria-hidden />
          <span className="text-slate-600 dark:text-slate-300">Transcribing your recording…</span>
        </>
      )}
    </div>
  )
}
