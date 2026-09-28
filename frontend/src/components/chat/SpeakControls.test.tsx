import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../services/api'
import * as voiceApi from '../../services/voice'
import { SpeakControls } from './SpeakControls'

vi.mock('../../services/voice', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/voice')>()),
  synthesizeSpeech: vi.fn(),
}))

/** jsdom has no media playback: emulate play/pause/ended with the real events. */
const players: HTMLMediaElement[] = []
function stubMedia() {
  let paused = new WeakMap<HTMLMediaElement, boolean>()
  let ended = new WeakMap<HTMLMediaElement, boolean>()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    if (!players.includes(this)) {
      players.push(this)
      Object.defineProperty(this, 'duration', { value: 12.4, configurable: true })
      this.dispatchEvent(new Event('loadedmetadata'))
    }
    paused.set(this, false)
    ended.set(this, false)
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    if (paused.get(this) === false) {
      paused.set(this, true)
      this.dispatchEvent(new Event('pause'))
    }
  })
  vi.spyOn(HTMLMediaElement.prototype, 'paused', 'get').mockImplementation(function (this: HTMLMediaElement) {
    return paused.get(this) ?? true
  })
  vi.spyOn(HTMLMediaElement.prototype, 'ended', 'get').mockImplementation(function (this: HTMLMediaElement) {
    return ended.get(this) ?? false
  })
  return {
    finish(media: HTMLMediaElement) {
      ended.set(media, true)
      paused.set(media, true)
      media.dispatchEvent(new Event('pause'))
      media.dispatchEvent(new Event('ended'))
    },
    reset() {
      paused = new WeakMap()
      ended = new WeakMap()
    },
  }
}

const speech = (truncated = false) => ({ audio: new Blob(['mp3'], { type: 'audio/mpeg' }), truncated })

describe('SpeakControls', () => {
  let media: ReturnType<typeof stubMedia>
  beforeEach(() => {
    players.length = 0
    media = stubMedia()
    vi.mocked(voiceApi.synthesizeSpeech).mockReset().mockResolvedValue(speech())
  })
  afterEach(() => {
    cleanup() // unmount (which pauses) while the media stubs are still installed
    media.reset()
    vi.restoreAllMocks()
  })

  it('does nothing until the user asks to listen', () => {
    render(<SpeakControls text="Hello." />)

    expect(screen.getByRole('button', { name: 'Listen' })).toBeInTheDocument()
    expect(voiceApi.synthesizeSpeech).not.toHaveBeenCalled()
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  })

  it('listens, pauses, resumes, stops and replays', async () => {
    render(<SpeakControls text="You get **25 days** [1]." />)

    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))

    const group = await screen.findByRole('group', { name: 'Answer audio' })
    expect(voiceApi.synthesizeSpeech).toHaveBeenCalledWith('You get **25 days** [1].', expect.any(AbortSignal))
    expect(within(group).getByText('0:00 / 0:12')).toBeInTheDocument()
    const audio = players[0]

    await userEvent.click(within(group).getByRole('button', { name: 'Pause' }))
    expect(audio.paused).toBe(true)

    await userEvent.click(within(group).getByRole('button', { name: 'Play' }))
    expect(audio.paused).toBe(false)
    act(() => {
      audio.currentTime = 5
      audio.dispatchEvent(new Event('timeupdate'))
    })
    expect(within(group).getByText('0:05 / 0:12')).toBeInTheDocument()

    await userEvent.click(within(group).getByRole('button', { name: 'Stop' }))
    expect(audio.paused).toBe(true)
    expect(audio.currentTime).toBe(0)
    expect(within(group).getByText('0:00 / 0:12')).toBeInTheDocument()

    await userEvent.click(within(group).getByRole('button', { name: 'Replay from the start' }))
    expect(audio.paused).toBe(false)
    // Audio is fetched once; Replay reuses it.
    expect(voiceApi.synthesizeSpeech).toHaveBeenCalledTimes(1)
    expect(players).toHaveLength(1)
  })

  it('seeks with the progress bar and plays again after the end', async () => {
    render(<SpeakControls text="Hello." />)
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    const slider = await screen.findByRole('slider', { name: 'Playback position' })
    const audio = players[0]

    fireEvent.change(slider, { target: { value: '8' } })
    expect(audio.currentTime).toBe(8)

    act(() => media.finish(audio))
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(audio.currentTime).toBe(0)
    expect(audio.paused).toBe(false)
  })

  it('plays only one answer at a time', async () => {
    render(
      <>
        <div data-testid="first">
          <SpeakControls text="First answer." />
        </div>
        <div data-testid="second">
          <SpeakControls text="Second answer." />
        </div>
      </>,
    )
    await userEvent.click(within(screen.getByTestId('first')).getByRole('button', { name: 'Listen' }))
    await within(screen.getByTestId('first')).findByRole('button', { name: 'Pause' })

    await userEvent.click(within(screen.getByTestId('second')).getByRole('button', { name: 'Listen' }))
    await within(screen.getByTestId('second')).findByRole('button', { name: 'Pause' })

    expect(players[0].paused).toBe(true)
    expect(players[1].paused).toBe(false)
    expect(within(screen.getByTestId('first')).getByRole('button', { name: 'Play' })).toBeInTheDocument()
  })

  it('stops and frees the audio when the answer is removed', async () => {
    const revoke = vi.spyOn(URL, 'revokeObjectURL')
    const { unmount } = render(<SpeakControls text="Hello." />)
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    await screen.findByRole('button', { name: 'Pause' })

    unmount()

    expect(players[0].paused).toBe(true)
    expect(revoke).toHaveBeenCalledWith(players[0].src)
  })

  it('says when only the first part is read', async () => {
    vi.mocked(voiceApi.synthesizeSpeech).mockResolvedValue(speech(true))
    render(<SpeakControls text="Long answer." />)

    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))

    expect(await screen.findByText('Reading the first part only')).toBeInTheDocument()
  })

  it('shows the server’s error and lets the user try again', async () => {
    vi.mocked(voiceApi.synthesizeSpeech).mockRejectedValueOnce(
      new ApiError(502, 'tts_error', 'Speech could not be generated. Please try again.', null),
    )
    render(<SpeakControls text="Hello." />)

    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Speech could not be generated')
    await userEvent.click(screen.getByRole('button', { name: 'Listen' }))
    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
