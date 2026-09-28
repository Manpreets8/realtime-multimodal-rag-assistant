import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../services/api'
import { MAX_RECORDING_SECONDS, transcribeAudio } from '../services/voice'

export type RecorderStatus = 'idle' | 'requesting' | 'recording' | 'transcribing'

// Preferred first; the browser picks what it supports (Chrome/Firefox: Opus, Safari: MP4/AAC).
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/mp4']

export function microphoneErrorMessage(error: unknown): string {
  const name = error instanceof DOMException || error instanceof Error ? error.name : ''
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone access is blocked. Allow it in your browser’s site settings and try again.'
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found. Connect one and try again.'
    case 'NotReadableError':
      return 'The microphone is being used by another application.'
    default:
      return 'The microphone could not be started.'
  }
}

export function voiceSupportProblem(): string | null {
  if (typeof window === 'undefined') return 'Voice input is not available.'
  if (!window.isSecureContext) return 'Voice input needs a secure (HTTPS) connection.'
  if (!navigator.mediaDevices?.getUserMedia || typeof window.MediaRecorder === 'undefined') {
    return 'This browser does not support voice recording.'
  }
  return null
}

/**
 * Record from the microphone, then transcribe on the server. The transcript is handed
 * to `onTranscript` for the user to review; nothing is sent automatically.
 */
export function useVoiceRecorder(onTranscript: (text: string) => void, maxSeconds = MAX_RECORDING_SECONDS) {
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const cancelled = useRef(false)
  const onTranscriptRef = useRef(onTranscript)

  useEffect(() => {
    onTranscriptRef.current = onTranscript
  }, [onTranscript])

  const releaseMicrophone = useCallback(() => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
    stream.current?.getTracks().forEach((track) => track.stop()) // turns off the browser's mic indicator
    stream.current = null
  }, [])

  const transcribe = useCallback(async (recording: Blob) => {
    setStatus('transcribing')
    try {
      const result = await transcribeAudio(recording)
      onTranscriptRef.current(result.text)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Transcription failed. Please try again.')
    } finally {
      setStatus('idle')
    }
  }, [])

  const start = useCallback(async () => {
    const problem = voiceSupportProblem()
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    setStatus('requesting')
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
    } catch (err) {
      setError(microphoneErrorMessage(err))
      setStatus('idle')
      return
    }
    const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported?.(type))
    const media = new MediaRecorder(stream.current, mimeType ? { mimeType } : undefined)
    const chunks: Blob[] = []
    cancelled.current = false
    media.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    media.onstop = () => {
      releaseMicrophone()
      recorder.current = null
      if (cancelled.current || chunks.length === 0) {
        setStatus('idle')
        return
      }
      void transcribe(new Blob(chunks, { type: media.mimeType || mimeType || 'audio/webm' }))
    }
    recorder.current = media
    media.start()
    let seconds = 0
    setElapsed(0)
    setStatus('recording')
    timer.current = setInterval(() => {
      seconds += 1
      setElapsed(seconds)
      if (seconds >= maxSeconds && media.state === 'recording') media.stop() // auto-stop at the limit
    }, 1000)
  }, [maxSeconds, releaseMicrophone, transcribe])

  const stop = useCallback(() => {
    if (recorder.current?.state === 'recording') recorder.current.stop()
  }, [])

  const cancel = useCallback(() => {
    cancelled.current = true
    if (recorder.current?.state === 'recording') recorder.current.stop()
    else releaseMicrophone()
  }, [releaseMicrophone])

  // Never leave the microphone on after the composer goes away.
  useEffect(() => () => {
    cancelled.current = true
    if (recorder.current?.state === 'recording') recorder.current.stop()
    releaseMicrophone()
  }, [releaseMicrophone])

  return { status, elapsed, error, clearError: () => setError(null), start, stop, cancel }
}
