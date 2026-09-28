import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../services/api'
import * as voiceApi from '../services/voice'
import { TEST_USER, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

vi.mock('../services/voice', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/voice')>()),
  transcribeAudio: vi.fn(),
}))

/** Minimal MediaRecorder: emits one chunk when stopped. */
class FakeMediaRecorder {
  static last: FakeMediaRecorder
  static isTypeSupported = (type: string) => type === 'audio/webm;codecs=opus'
  state: 'inactive' | 'recording' = 'inactive'
  mimeType: string
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? ''
    FakeMediaRecorder.last = this
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio-bytes'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

const track = { stop: vi.fn() }
const getUserMedia = vi.fn()

function backend() {
  mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /conversations': () => json([]),
    'GET /knowledge-bases': () => json([]),
  })
}

const composer = () => screen.getByLabelText('Message', { exact: true })

describe('Voice input', () => {
  beforeEach(() => {
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
    getUserMedia.mockReset().mockResolvedValue({ getTracks: () => [track] })
    track.stop.mockReset()
    vi.mocked(voiceApi.transcribeAudio).mockReset()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('records, transcribes and puts the transcript in the composer for review', async () => {
    vi.mocked(voiceApi.transcribeAudio).mockResolvedValue({
      text: 'How many days of annual leave do I get?', language: 'en', language_probability: 0.99,
      duration_seconds: 3.2, model: 'faster-whisper/base', processing_ms: 900,
    })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))
    expect(await screen.findByText('Recording')).toBeInTheDocument()
    expect(getUserMedia).toHaveBeenCalledWith({ audio: { echoCancellation: true, noiseSuppression: true } })
    expect(FakeMediaRecorder.last.mimeType).toBe('audio/webm;codecs=opus')
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Stop & transcribe' }))

    await waitFor(() => expect(composer()).toHaveValue('How many days of annual leave do I get?'))
    const [recording] = vi.mocked(voiceApi.transcribeAudio).mock.calls[0]
    expect(recording.type).toBe('audio/webm;codecs=opus')
    expect(track.stop).toHaveBeenCalled() // microphone released
    expect(screen.queryByText('Recording')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled() // user sends when ready
  })

  it('appends to text already typed', async () => {
    vi.mocked(voiceApi.transcribeAudio).mockResolvedValue({
      text: 'and can unused days carry over?', language: 'en', language_probability: 1, duration_seconds: 2,
      model: 'm', processing_ms: 1,
    })
    backend()
    renderSignedIn('/chat')
    await userEvent.type(await screen.findByLabelText('Message', { exact: true }), 'Annual leave:')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Stop & transcribe' }))

    await waitFor(() => expect(composer()).toHaveValue('Annual leave: and can unused days carry over?'))
  })

  it('cancel discards the recording without transcribing', async () => {
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(voiceApi.transcribeAudio).not.toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalled()
    expect(screen.queryByText('Recording')).not.toBeInTheDocument()
  })

  it('explains a blocked microphone', async () => {
    getUserMedia.mockRejectedValue(new DOMException('denied', 'NotAllowedError'))
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Microphone access is blocked')
  })

  it('explains when no microphone exists', async () => {
    getUserMedia.mockRejectedValue(new DOMException('none', 'NotFoundError'))
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('No microphone was found')
  })

  it('explains an insecure connection before asking for the microphone', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('needs a secure (HTTPS) connection')
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it('shows the server’s reason when nothing was heard', async () => {
    vi.mocked(voiceApi.transcribeAudio).mockRejectedValue(
      new ApiError(422, 'no_speech', 'No speech was detected. Check your microphone and try again.', null),
    )
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Stop & transcribe' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('No speech was detected')
    expect(composer()).toHaveValue('')
  })

  it('stops automatically at the maximum length', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(voiceApi.transcribeAudio).mockResolvedValue({
      text: 'long dictation', language: 'en', language_probability: 1, duration_seconds: 120, model: 'm', processing_ms: 1,
    })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')
    await userEvent.click(screen.getByRole('button', { name: 'Record a voice message' }))
    await screen.findByText('Recording')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(voiceApi.MAX_RECORDING_SECONDS * 1000)
    })

    await waitFor(() => expect(composer()).toHaveValue('long dictation'))
    expect(FakeMediaRecorder.last.state).toBe('inactive')
  })
})
