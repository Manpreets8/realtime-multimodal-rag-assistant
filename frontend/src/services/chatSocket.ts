/**
 * Streaming chat over `/api/v1/ws/chat` (protocol documented in backend/app/api/routes/chat_socket.py).
 *
 * One connection is shared by the app, opened on the first message and kept open. If the
 * WebSocket can't be used (blocked by a proxy, unsupported, server unreachable) a message is
 * sent with `POST /chat` instead, so chat keeps working, just without live progress.
 */

import { API_BASE_URL, ApiError, toApiError } from './api'
import { chatRequestBody, sendMessage, type ChatResponse, type SendMessageInput } from './chat'
import { tokenStorage } from './tokenStorage'

export type ChatStage = 'rewriting' | 'retrieving' | 'reranking' | 'generating'

export interface StreamSource {
  number: number
  filename: string
  page_number: number | null
  section: string | null
}

export interface ChatStreamHandlers {
  onStage?: (stage: ChatStage) => void
  onSources?: (sources: StreamSource[]) => void
  onDelta?: (text: string) => void
  /** Discard the text streamed so far (a fallback model took over mid-answer). */
  onRestart?: () => void
}

export interface ChatStream {
  /** The saved turn. Rejects with `ChatCancelledError` after `cancel()`, or an `ApiError`. */
  result: Promise<ChatResponse>
  /** Stop generating. Nothing is saved. */
  cancel: () => void
}

export class ChatCancelledError extends Error {
  constructor() {
    super('The answer was stopped.')
    this.name = 'ChatCancelledError'
  }
}

/** The WebSocket could not be used; nothing was sent, so the REST endpoint can be tried. */
class SocketUnavailableError extends Error {}

const CONNECT_TIMEOUT_MS = 5000
/** After a failed connection, use REST for a while instead of waiting for the socket each time. */
const RETRY_SOCKET_AFTER_MS = 60_000

export const CONNECTION_LOST_MESSAGE = 'The connection was lost before the answer finished. Nothing was saved.'

function socketUrl(): string {
  const base = new URL(API_BASE_URL, window.location.href)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${base.href.replace(/\/$/, '')}/ws/chat`
}

interface Turn {
  handlers: ChatStreamHandlers
  resolve: (response: ChatResponse) => void
  reject: (error: Error) => void
}

interface ServerEvent {
  type: string
  id?: string | null
  stage?: ChatStage
  sources?: StreamSource[]
  text?: string
  response?: ChatResponse
  error?: { code: string; message: string; request_id?: string | null; details?: unknown }
}

class ChatSocket {
  private socket: WebSocket | null = null
  private ready: Promise<WebSocket> | null = null
  private token: string | null = null
  private turns = new Map<string, Turn>()
  private unavailableUntil = 0

  private connect(): Promise<WebSocket> {
    const token = tokenStorage.get()
    if (this.ready && token === this.token) return this.ready
    this.close() // first use, or a different session
    if (typeof WebSocket === 'undefined' || Date.now() < this.unavailableUntil) {
      return Promise.reject(new SocketUnavailableError())
    }
    this.token = token
    const ready = new Promise<WebSocket>((resolve, reject) => {
      let socket: WebSocket
      try {
        socket = new WebSocket(socketUrl())
      } catch {
        this.unavailableUntil = Date.now() + RETRY_SOCKET_AFTER_MS
        reject(new SocketUnavailableError())
        return
      }
      this.socket = socket
      let authenticated = false
      const timer = window.setTimeout(() => socket.close(), CONNECT_TIMEOUT_MS)

      socket.onopen = () => socket.send(JSON.stringify({ type: 'auth', token }))
      socket.onmessage = (message) => {
        const event = JSON.parse(String(message.data)) as ServerEvent
        if (authenticated) {
          this.dispatch(event)
        } else if (event.type === 'ready') {
          authenticated = true
          window.clearTimeout(timer)
          resolve(socket)
        } else if (event.type === 'error') {
          window.clearTimeout(timer)
          reject(toApiError(401, event, null, Boolean(token))) // signs out, like a REST 401
        }
      }
      socket.onclose = (close) => {
        window.clearTimeout(timer)
        if (this.socket === socket) {
          this.socket = null
          this.ready = null
        }
        if (!authenticated) {
          if (close.code !== 4401) {
            this.unavailableUntil = Date.now() + RETRY_SOCKET_AFTER_MS
            reject(new SocketUnavailableError())
          }
          return
        }
        if (close.code === 4401) toApiError(401, null, null, true) // session ended: sign out
        this.failAll(new ApiError(0, 'connection_lost', CONNECTION_LOST_MESSAGE, null))
      }
    })
    this.ready = ready
    ready.catch(() => {
      if (this.ready === ready) this.ready = null // retry on the next message
    })
    return ready
  }

  private dispatch(event: ServerEvent) {
    const turn = event.id ? this.turns.get(event.id) : undefined
    if (!turn || !event.id) return
    switch (event.type) {
      case 'status':
        turn.handlers.onStage?.(event.stage!)
        break
      case 'sources':
        turn.handlers.onSources?.(event.sources ?? [])
        break
      case 'delta':
        turn.handlers.onDelta?.(event.text ?? '')
        break
      case 'restart':
        turn.handlers.onRestart?.()
        break
      case 'done':
        this.turns.delete(event.id)
        turn.resolve(event.response!)
        break
      case 'cancelled':
        this.turns.delete(event.id)
        turn.reject(new ChatCancelledError())
        break
      case 'error': {
        this.turns.delete(event.id)
        const error = event.error!
        turn.reject(new ApiError(0, error.code, error.message, error.request_id ?? null, error.details))
        break
      }
    }
  }

  private failAll(error: Error) {
    const turns = [...this.turns.values()]
    this.turns.clear()
    turns.forEach((turn) => turn.reject(error))
  }

  /** Throws `SocketUnavailableError` (before anything is sent) when the socket can't be used. */
  send(input: SendMessageInput, handlers: ChatStreamHandlers): ChatStream {
    const id = crypto.randomUUID()
    let cancelled = false
    let sent = false
    let turn!: Turn
    const result = new Promise<ChatResponse>((resolve, reject) => {
      turn = { handlers, resolve, reject }
    })
    const started = this.connect().then((socket) => {
      if (cancelled) throw new ChatCancelledError()
      this.turns.set(id, turn)
      socket.send(JSON.stringify({ type: 'chat', id, ...chatRequestBody(input) }))
      sent = true
    })
    return {
      result: started.then(() => result),
      cancel: () => {
        cancelled = true
        if (sent && this.turns.has(id)) this.socket?.send(JSON.stringify({ type: 'cancel', id }))
      },
    }
  }

  close() {
    const socket = this.socket
    this.socket = null
    this.ready = null
    this.token = null
    this.unavailableUntil = 0 // a new session tries the WebSocket again
    if (socket) {
      socket.onclose = null
      socket.close(1000)
    }
    this.failAll(new ApiError(0, 'connection_lost', CONNECTION_LOST_MESSAGE, null))
  }
}

const shared = new ChatSocket()

/** Send a chat message, streaming progress when the WebSocket is available. */
export function streamMessage(input: SendMessageInput, handlers: ChatStreamHandlers = {}): ChatStream {
  const stream = shared.send(input, handlers)
  let cancelledBeforeFallback = false
  const result = stream.result.catch((error: unknown) => {
    if (!(error instanceof SocketUnavailableError)) throw error
    if (cancelledBeforeFallback) throw new ChatCancelledError()
    return sendMessage(input) // no live progress, but the same saved turn
  })
  return {
    result,
    cancel: () => {
      cancelledBeforeFallback = true
      stream.cancel()
    },
  }
}

/** Close the shared connection (sign-out). In-flight answers are abandoned and not saved. */
export function closeChatSocket() {
  shared.close()
}
