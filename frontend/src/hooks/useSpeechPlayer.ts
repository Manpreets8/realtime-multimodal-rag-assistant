import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../services/api'
import { synthesizeSpeech } from '../services/voice'

export type SpeechStatus = 'idle' | 'loading' | 'playing' | 'paused' | 'ended'

/** Only one answer is heard at a time: starting a player pauses whichever one was playing. */
let activePause: (() => void) | null = null

function claimPlayback(pause: () => void) {
  if (activePause && activePause !== pause) activePause()
  activePause = pause
}

function releasePlayback(pause: () => void) {
  if (activePause === pause) activePause = null
}

/**
 * Reads `text` aloud on request. Nothing plays until the user presses Listen; the audio is
 * fetched once and kept for Replay until the component unmounts.
 */
export function useSpeechPlayer(text: string) {
  const [status, setStatus] = useState<SpeechStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const pause = useCallback(() => {
    audioRef.current?.pause()
  }, [])

  const playAudio = useCallback(
    async (audio: HTMLAudioElement) => {
      claimPlayback(pause)
      try {
        await audio.play()
      } catch (err) {
        // Browsers reject play() when a newer pause() interrupts it; that is not an error for the user.
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          setError('Audio playback failed in this browser.')
          setStatus('idle')
        }
      }
    },
    [pause],
  )

  const listen = useCallback(async () => {
    setError(null)
    if (audioRef.current) {
      await playAudio(audioRef.current)
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setStatus('loading')
    try {
      const speech = await synthesizeSpeech(text, controller.signal)
      const url = URL.createObjectURL(speech.audio)
      urlRef.current = url
      const audio = new Audio(url)
      audio.preload = 'auto'
      audio.addEventListener('play', () => setStatus('playing'))
      audio.addEventListener('pause', () => setStatus((s) => (audio.ended || s === 'ended' ? 'ended' : 'paused')))
      audio.addEventListener('ended', () => {
        setStatus('ended')
        releasePlayback(pause)
      })
      audio.addEventListener('timeupdate', () => setPosition(audio.currentTime))
      audio.addEventListener('loadedmetadata', () => setDuration(Number.isFinite(audio.duration) ? audio.duration : 0))
      audioRef.current = audio
      setTruncated(speech.truncated)
      await playAudio(audio) // started by the user's click on Listen
    } catch (err) {
      if (controller.signal.aborted) return
      setError(err instanceof ApiError ? err.message : 'Speech could not be generated. Please try again.')
      setStatus('idle')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [text, playAudio, pause])

  const stop = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.pause()
    audio.currentTime = 0
    setPosition(0)
    setStatus('paused')
    releasePlayback(pause)
  }, [pause])

  const replay = useCallback(async () => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = 0
    setPosition(0)
    await playAudio(audio)
  }, [playAudio])

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = seconds
    setPosition(seconds)
  }, [])

  // Stop playback, cancel a pending request and free the audio when the answer leaves the screen.
  useEffect(
    () => () => {
      abortRef.current?.abort()
      audioRef.current?.pause()
      releasePlayback(pause)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    },
    [pause],
  )

  return { status, error, truncated, position, duration, listen, pause, stop, replay, seek, clearError: () => setError(null) }
}
