import type { ReactNode } from 'react'

import { useSpeechPlayer } from '../../hooks/useSpeechPlayer'

function clock(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

const iconButton =
  'flex size-7 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-800'

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="size-4" aria-hidden>
      {children}
    </svg>
  )
}

/** "Listen" for an answer, then Play/Pause, Stop, Replay and a seekable progress bar. Never auto-plays. */
export function SpeakControls({ text }: { text: string }) {
  const player = useSpeechPlayer(text)
  const { status, position, duration } = player

  if (status === 'idle' || status === 'loading') {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          type="button"
          onClick={() => void player.listen()}
          disabled={status === 'loading'}
          className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-3.5" aria-hidden>
            <path d="M11 5 6 9H3v6h3l5 4V5z" strokeLinejoin="round" />
            <path d="M15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13" strokeLinecap="round" />
          </svg>
          {status === 'loading' ? 'Preparing audio…' : 'Listen'}
        </button>
        {player.error && (
          <span className="text-xs text-rose-600 dark:text-rose-400" role="alert">
            {player.error}
          </span>
        )}
      </span>
    )
  }

  const playing = status === 'playing'
  return (
    <span className="flex max-w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      <span
        className="flex max-w-full min-w-0 items-center gap-1 rounded-full border border-slate-200 py-0.5 pr-3 pl-1 dark:border-slate-700"
        role="group"
        aria-label="Answer audio"
      >
        {playing ? (
          <button type="button" onClick={player.pause} className={iconButton} aria-label="Pause">
            <Icon>
              <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" />
            </Icon>
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void (status === 'ended' ? player.replay() : player.listen())}
            className={iconButton}
            aria-label="Play"
          >
            <Icon>
              <path d="M8 5v14l11-7z" />
            </Icon>
          </button>
        )}
        <button type="button" onClick={player.stop} disabled={position === 0 && !playing} className={iconButton} aria-label="Stop">
          <Icon>
            <path d="M7 7h10v10H7z" />
          </Icon>
        </button>
        <button type="button" onClick={() => void player.replay()} className={iconButton} aria-label="Replay from the start">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-4" aria-hidden>
            <path d="M4 12a8 8 0 108-8H8M8 4 5 7l3 3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={Math.min(position, duration || 0)}
          onChange={(event) => player.seek(Number(event.target.value))}
          disabled={!duration}
          aria-label="Playback position"
          aria-valuetext={`${clock(position)} of ${clock(duration)}`}
          className="mx-1 h-1 w-16 min-w-0 flex-1 cursor-pointer accent-brand-600 sm:w-40 sm:flex-none"
        />
        <span className="shrink-0 text-xs text-slate-500 tabular-nums dark:text-slate-400">
          {clock(position)} / {clock(duration)}
        </span>
      </span>
      {player.truncated && <span className="text-xs text-amber-700 dark:text-amber-400">Reading the first part only</span>}
      {player.error && (
        <span className="text-xs text-rose-600 dark:text-rose-400" role="alert">
          {player.error}
        </span>
      )}
    </span>
  )
}
