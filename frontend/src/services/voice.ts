import { LONG_TIMEOUT_MS, apiBlob, uploadForm } from './api'

export interface Transcription {
  text: string
  language: string | null
  language_probability: number | null
  duration_seconds: number
  model: string
  processing_ms: number
}

/** Mirrors MAX_AUDIO_SECONDS; recording stops automatically at this length. */
export const MAX_RECORDING_SECONDS = 120

const EXTENSIONS: Record<string, string> = { 'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav' }

export function transcribeAudio(recording: Blob): Promise<Transcription> {
  const extension = EXTENSIONS[recording.type.split(';')[0]] ?? 'webm'
  const form = new FormData()
  form.append('file', recording, `recording.${extension}`)
  return uploadForm<Transcription>('/voice/transcribe', form, { timeoutMs: LONG_TIMEOUT_MS })
}

export interface SynthesizedSpeech {
  audio: Blob
  /** The text was longer than the server's limit, so only the first part is read. */
  truncated: boolean
}

/** Read answer text aloud. The server removes markdown and citation markers before speaking. */
export async function synthesizeSpeech(text: string, signal?: AbortSignal): Promise<SynthesizedSpeech> {
  const { blob, headers } = await apiBlob('/voice/synthesize', { json: { text }, signal, timeoutMs: LONG_TIMEOUT_MS })
  return { audio: blob, truncated: headers.get('X-Speech-Truncated') === 'true' }
}
