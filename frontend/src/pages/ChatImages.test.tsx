import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatMessage, ChatResponse } from '../services/chat'
import * as imagesApi from '../services/images'
import { TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

vi.mock('../services/images', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/images')>()),
  uploadImage: vi.fn(),
  deleteImage: vi.fn(),
}))

const png = (name = 'screen.png', size = 1000) => new File([new Uint8Array(size)], name, { type: 'image/png' })

function message(id: string, role: 'user' | 'assistant', content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id, role, content, created_at: '2026-09-27T10:00:00Z', images: [], answer_type: null, grounded: false,
    knowledge_base_id: null, retrieval_query: null, model: null, usage: null, truncated: false,
    timings_ms: null, retrieval: null, citations: [], sources: [], ...extra,
  }
}

function chatResponse(text: string, imageIds: string[]): ChatResponse {
  return {
    conversation: {
      id: 'conv-img', title: text || 'Image', knowledge_base_id: null, knowledge_base_name: null, message_count: 2,
      last_message_preview: 'A login form.', created_at: '2026-09-27T10:00:00Z', updated_at: '2026-09-27T10:00:00Z',
    },
    user_message: message('u1', 'user', text, {
      images: imageIds.map((id) => ({ id, filename: 'screen.png', media_type: 'image/png', width: 800, height: 600 })),
    }),
    assistant_message: message('a1', 'assistant', 'A login form with an error: ERR-4521.', { answer_type: 'image' }),
  }
}

function stored(text: string, imageIds: string[]) {
  const response = chatResponse(text, imageIds)
  return { ...response.conversation, messages: [response.user_message, response.assistant_message] }
}

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(TEST_USER),
    'GET /conversations': () => json([]),
    'GET /knowledge-bases': () => json([]),
    // Plain bytes: Node's Response does not accept jsdom's Blob.
    'GET /images/img-1/content': () => new Response(new Uint8Array([137, 80, 78, 71]), { headers: { 'Content-Type': 'image/png' } }),
    ...extra,
  })
}

function lastChatBody(fetchSpy: ReturnType<typeof mockFetch>) {
  const call = fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/chat')).at(-1)
  return call ? JSON.parse(String(call[1]?.body)) : undefined
}

async function attach(...files: File[]) {
  const user = userEvent.setup({ applyAccept: false })
  await user.upload(screen.getByTestId('image-input'), files)
}

describe('Chat with images', () => {
  beforeEach(() => {
    vi.mocked(imagesApi.uploadImage).mockReset()
    vi.mocked(imagesApi.deleteImage).mockReset().mockResolvedValue(null)
  })

  it('uploads an attached image and sends its id with the question', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-1', filename: 'screen.png', media_type: 'image/png', width: 800, height: 600, size_bytes: 1000,
    })
    const fetchSpy = backend({
      'POST /chat': () => json(chatResponse('What error is shown?', ['img-1'])),
      'GET /conversations/conv-img': () => json(stored('What error is shown?', ['img-1'])),
    })
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await attach(png())
    const chips = await screen.findByRole('list', { name: 'Attached images' })
    await waitFor(() => expect(within(chips).queryByRole('status')).not.toBeInTheDocument()) // upload finished
    await userEvent.type(screen.getByLabelText('Message', { exact: true }), 'What error is shown?{Enter}')

    expect(await screen.findByText('Answered from your image')).toBeInTheDocument()
    expect(lastChatBody(fetchSpy)).toEqual({ message: 'What error is shown?', image_ids: ['img-1'], knowledge_base_id: null })
    const sent = screen.getByRole('article', { name: 'Your message' })
    expect(await within(sent).findByRole('img', { name: 'screen.png' })).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Attached images' })).not.toBeInTheDocument()
  })

  it('allows sending an image without text', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-1', filename: 'screen.png', media_type: 'image/png', width: 800, height: 600, size_bytes: 1000,
    })
    const fetchSpy = backend({ 'POST /chat': () => json(chatResponse('', ['img-1'])) })
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await attach(png())
    const send = screen.getByRole('button', { name: 'Send message' })
    await waitFor(() => expect(send).toBeEnabled())
    await userEvent.click(send)

    await waitFor(() => expect(lastChatBody(fetchSpy)).toEqual({ message: '', image_ids: ['img-1'], knowledge_base_id: null }))
  })

  it('rejects unsupported files and too many images without uploading them', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-x', filename: 'a.png', media_type: 'image/png', width: 1, height: 1, size_bytes: 1,
    })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await attach(new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' }))
    expect(screen.getByRole('alert')).toHaveTextContent('logo.svg: Only PNG, JPEG, GIF and WebP images are supported.')

    await attach(png('huge.png', 6 * 1024 * 1024))
    expect(screen.getByRole('alert')).toHaveTextContent('huge.png: Images must be at most 5 MB.')
    expect(imagesApi.uploadImage).not.toHaveBeenCalled()

    await attach(png('1.png'), png('2.png'), png('3.png'), png('4.png'), png('5.png'))
    expect(screen.getByRole('alert')).toHaveTextContent('up to 4 images')
    expect(imagesApi.uploadImage).toHaveBeenCalledTimes(4)
  })

  it('removing an uploaded image deletes it on the server', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-1', filename: 'screen.png', media_type: 'image/png', width: 1, height: 1, size_bytes: 1,
    })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')
    await attach(png())
    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Attached images' })).queryByRole('status')).not.toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Remove screen.png' }))

    expect(imagesApi.deleteImage).toHaveBeenCalledWith('img-1')
    expect(screen.queryByRole('list', { name: 'Attached images' })).not.toBeInTheDocument()
  })

  it('cannot send while an image is still uploading', async () => {
    vi.mocked(imagesApi.uploadImage).mockReturnValue(new Promise(() => undefined)) // never finishes
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    await attach(png())
    await userEvent.type(screen.getByLabelText('Message', { exact: true }), 'What is this?')

    expect(screen.getByRole('status', { name: 'Uploading screen.png' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  })

  it('keeps images with a failed message and retries with the same ids', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-1', filename: 'screen.png', media_type: 'image/png', width: 1, height: 1, size_bytes: 1,
    })
    let attempts = 0
    const fetchSpy = backend({
      'POST /chat': () =>
        ++attempts === 1
          ? errorEnvelope(504, 'llm_timeout', 'The AI model took too long to respond.')
          : json(chatResponse('What is this?', ['img-1'])),
      'GET /conversations/conv-img': () => json(stored('What is this?', ['img-1'])),
    })
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')
    await attach(png())
    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Attached images' })).queryByRole('status')).not.toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('Message', { exact: true }), 'What is this?{Enter}')

    const alert = await screen.findByRole('alert')
    expect(within(screen.getByRole('article', { name: 'Your message' })).getByRole('img', { name: 'screen.png' })).toBeInTheDocument()
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Answered from your image')).toBeInTheDocument()
    expect(lastChatBody(fetchSpy).image_ids).toEqual(['img-1'])
  })

  it('pasting an image attaches it', async () => {
    vi.mocked(imagesApi.uploadImage).mockResolvedValue({
      id: 'img-1', filename: 'pasted.png', media_type: 'image/png', width: 1, height: 1, size_bytes: 1,
    })
    backend()
    renderSignedIn('/chat')
    await screen.findByText('How can I help?')

    fireEvent.paste(screen.getByLabelText('Message', { exact: true }), { clipboardData: { files: [png('pasted.png')] } })

    expect(await screen.findByRole('img', { name: 'pasted.png' })).toBeInTheDocument()
    expect(imagesApi.uploadImage).toHaveBeenCalledOnce()
  })
})
