import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { NavLink, useNavigate, useParams, useSearchParams } from 'react-router'

import { AssistantShell, ChatMessageView, UserBubble } from '../components/chat/ChatMessageView'
import { AttachmentChips } from '../components/chat/ImageThumbs'
import { renderAnswer } from '../components/citations/answerMarkdown'
import { MicButton, RecordingBar } from '../components/chat/VoiceControls'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { useImageAttachments, type Attachment } from '../hooks/useImageAttachments'
import { useResource } from '../hooks/useResource'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import { useToast } from '../hooks/useToast'
import { ApiError } from '../services/api'
import {
  deleteConversation,
  getConversation,
  listConversations,
  updateConversation,
  type ConversationDetail,
  type ConversationSummary,
} from '../services/chat'
import { ChatCancelledError, streamMessage, type ChatStage, type ChatStream } from '../services/chatSocket'
import { IMAGE_TYPES } from '../services/images'
import { listKnowledgeBases } from '../services/knowledgeBases'
import { lastKnowledgeBase } from '../services/lastKnowledgeBase'
import { MAX_RECORDING_SECONDS } from '../services/voice'
import { formatRelative } from '../utils/format'

const MAX_MESSAGE_LENGTH = 2000

const SUGGESTIONS = {
  knowledgeBase: ['Summarize the main topics in these documents.', 'What are the most important rules or policies?'],
  general: ['Explain retrieval-augmented generation in simple terms.', 'Give me three tips for writing clear documentation.'],
}

interface Outgoing {
  text: string
  images: Attachment[]
}

/** Progress of the answer being generated (WebSocket streaming). */
interface LiveAnswer {
  stage: ChatStage | null
  sourceCount: number
  text: string
}

const NO_LIVE_ANSWER: LiveAnswer = { stage: null, sourceCount: 0, text: '' }

function stageLabel(live: LiveAnswer, usesKnowledgeBase: boolean): string {
  switch (live.stage) {
    case 'rewriting':
      return 'Understanding your follow-up…'
    case 'retrieving':
      return 'Searching your documents…'
    case 'reranking':
      return 'Ranking the most relevant passages…'
    case 'generating':
      return live.sourceCount
        ? `Writing an answer from ${live.sourceCount} passage${live.sourceCount === 1 ? '' : 's'}…`
        : 'Writing an answer…'
    default:
      return usesKnowledgeBase ? 'Searching your documents and writing an answer…' : 'Thinking…'
  }
}

function previews(images: Attachment[]) {
  if (images.length === 0) return undefined
  return images.map((image) => (
    <img
      key={image.localId}
      src={image.previewUrl}
      alt={image.file.name}
      className="size-20 rounded-lg border border-slate-200 object-cover dark:border-slate-700"
    />
  ))
}

function asApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null)
}

function ConversationList({
  conversations,
  activeId,
  onNew,
  onDelete,
  onNavigate,
}: {
  conversations: ConversationSummary[] | null
  activeId: string | undefined
  onNew: () => void
  onDelete: (conversation: ConversationSummary) => void
  onNavigate: () => void
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <Button onClick={onNew} className="w-full">
          New chat
        </Button>
      </div>
      <nav aria-label="Conversations" className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations === null ? (
          <p className="px-2 py-3 text-sm text-slate-500 dark:text-slate-400">Loading…</p>
        ) : conversations.length === 0 ? (
          <p className="px-2 py-3 text-sm text-slate-500 dark:text-slate-400">No conversations yet.</p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((conversation) => (
              <li key={conversation.id} className="group relative">
                <NavLink
                  to={`/chat/${conversation.id}`}
                  onClick={onNavigate}
                  className={`block rounded-lg px-3 py-2 pr-9 ${
                    conversation.id === activeId
                      ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-100'
                      : 'hover:bg-slate-100 dark:hover:bg-slate-800'
                  }`}
                >
                  <span className="block truncate text-sm font-medium">{conversation.title}</span>
                  <span className="block truncate text-xs text-slate-600 dark:text-slate-400">
                    {conversation.knowledge_base_name ?? 'General'} · {formatRelative(conversation.updated_at)}
                  </span>
                </NavLink>
                <button
                  type="button"
                  onClick={() => onDelete(conversation)}
                  aria-label={`Delete conversation ${conversation.title}`}
                  className="absolute top-2 right-1.5 rounded-md p-1.5 text-slate-500 hover:bg-slate-200 hover:text-rose-600 focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100 dark:hover:bg-slate-700 dark:text-slate-400"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-4" aria-hidden>
                    <path d="M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </div>
  )
}

export default function ChatPage() {
  const { conversationId } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()

  const conversations = useResource(listConversations)
  const knowledgeBases = useResource(listKnowledgeBases)
  const conversation = useResource(
    useCallback(
      () => (conversationId ? getConversation(conversationId) : Promise.resolve<ConversationDetail | null>(null)),
      [conversationId],
    ),
  )
  const detail = conversation.data && conversation.data.id === conversationId ? conversation.data : null

  // A link like /chat?kb=… picks the knowledge base; otherwise new chats start with the last one used.
  const [newChatKb, setNewChatKb] = useState<string | null>(() => searchParams.get('kb') ?? lastKnowledgeBase.get())
  const [input, setInput] = useState('')
  const attachments = useImageAttachments()
  const [pending, setPending] = useState<Outgoing | null>(null)
  const [live, setLive] = useState<LiveAnswer>(NO_LIVE_ANSWER)
  const [stopped, setStopped] = useState(false)
  const activeStream = useRef<ChatStream | null>(null)
  const [failed, setFailed] = useState<(Outgoing & { error: ApiError }) | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The transcript goes into the composer for review; the user decides when to send.
  const voice = useVoiceRecorder(
    useCallback((text: string) => {
      setInput((current) => (current.trim() ? `${current.trimEnd()} ${text}` : text))
      inputRef.current?.focus()
    }, []),
  )
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null)
  const [listOpen, setListOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // The remembered (or linked) knowledge base may have been deleted since: then it's a general chat.
  const newChatKbExists =
    newChatKb !== null && (knowledgeBases.data ? knowledgeBases.data.some((kb) => kb.id === newChatKb) : true)
  const validNewChatKb = newChatKbExists ? newChatKb : null
  const selectedKb = conversationId ? (detail?.knowledge_base_id ?? null) : validNewChatKb
  const messageCount = detail?.messages.length ?? 0

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messageCount, pending, failed])

  // Follow the streamed answer while the reader is at the bottom; don't pull them back if they scrolled up.
  useEffect(() => {
    const el = scrollRef.current
    if (!live.text || !el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [live.text])

  const setConversations = conversations.setData
  const upsertSummary = useCallback(
    (summary: ConversationSummary) =>
      setConversations((prev) => [summary, ...(prev ?? []).filter((c) => c.id !== summary.id)]),
    [setConversations],
  )

  async function send(text: string, images: Attachment[] = attachments.attachments) {
    const message = text.trim()
    const sent = images.filter((a) => a.state === 'ready')
    if ((!message && sent.length === 0) || pending || attachments.uploading) return
    setPending({ text: message, images: sent })
    setLive(NO_LIVE_ANSWER)
    setFailed(null)
    setStopped(false)
    setInput('')
    attachments.clear()
    const stream = streamMessage(
      {
        message,
        conversationId: conversationId ?? null,
        // A new conversation takes the selected knowledge base; existing ones keep theirs (changed via PATCH).
        knowledgeBaseId: conversationId ? undefined : validNewChatKb,
        imageIds: sent.map((a) => a.imageId!),
      },
      {
        onStage: (stage) => setLive((current) => ({ ...current, stage })),
        onSources: (sources) => setLive((current) => ({ ...current, sourceCount: sources.length })),
        onDelta: (text) => setLive((current) => ({ ...current, text: current.text + text })),
        onRestart: () => setLive((current) => ({ ...current, text: '' })),
      },
    )
    activeStream.current = stream
    try {
      const response = await stream.result
      const previous = detail?.messages ?? []
      conversation.setData({ ...response.conversation, messages: [...previous, response.user_message, response.assistant_message] })
      upsertSummary(response.conversation)
      sent.forEach((a) => URL.revokeObjectURL(a.previewUrl)) // the thread now shows the stored copies
      if (!conversationId) navigate(`/chat/${response.conversation.id}`, { replace: true })
    } catch (err) {
      if (err instanceof ChatCancelledError) {
        // Stopped by the user: nothing was saved; put the message back for editing.
        setInput(message)
        attachments.restore(sent)
        setStopped(true)
        inputRef.current?.focus()
      } else {
        // Nothing was saved: keep the text and images so the user can retry or edit.
        setFailed({ text: message, images: sent, error: asApiError(err) })
      }
    } finally {
      activeStream.current = null
      setPending(null)
      setLive(NO_LIVE_ANSWER)
    }
  }

  async function changeKnowledgeBase(value: string) {
    const kbId = value || null
    setSettingsError(null)
    lastKnowledgeBase.set(kbId)
    if (!conversationId || !detail) {
      setNewChatKb(kbId)
      return
    }
    try {
      const summary = await updateConversation(conversationId, { knowledge_base_id: kbId })
      conversation.setData({ ...detail, ...summary })
      upsertSummary(summary)
    } catch (err) {
      setSettingsError(asApiError(err).message)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send(input)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send(input)
    }
  }

  function autoGrow(el: HTMLTextAreaElement) {
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }

  const kbName = knowledgeBases.data?.find((kb) => kb.id === selectedKb)?.name
  const loadingConversation = Boolean(conversationId) && !detail && !conversation.error
  const title = detail?.title ?? 'New chat'
  const suggestions = selectedKb ? SUGGESTIONS.knowledgeBase : SUGGESTIONS.general

  return (
    <div className="flex h-[calc(100svh-57px)] lg:h-svh">
      {/* Conversation list: sidebar on md+, overlay on mobile. */}
      <aside
        className={`${listOpen ? 'fixed inset-0 z-40 flex bg-white dark:bg-slate-900' : 'hidden'} w-full flex-col border-r border-slate-200 md:static md:flex md:w-72 md:shrink-0 dark:border-slate-800`}
      >
        <ConversationList
          conversations={conversations.data}
          activeId={conversationId}
          onNew={() => {
            setListOpen(false)
            setFailed(null)
            navigate('/chat')
          }}
          onDelete={setDeleting}
          onNavigate={() => {
            setListOpen(false)
            setFailed(null)
          }}
        />
        {listOpen && (
          <button type="button" onClick={() => setListOpen(false)} className="border-t border-slate-200 p-3 text-sm md:hidden dark:border-slate-800">
            Close
          </button>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Chat">
        <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
          <button
            type="button"
            onClick={() => setListOpen(true)}
            className="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium md:hidden dark:border-slate-700"
          >
            Chats
          </button>
          <h1 className="min-w-0 flex-1 truncate font-semibold">{title}</h1>
          {/* Full-width second row on phones so the title keeps its space. */}
          <label className="flex w-full items-center gap-2 text-sm sm:w-auto">
            <span className="shrink-0 text-slate-500 dark:text-slate-400">Knowledge base</span>
            <select
              value={selectedKb ?? ''}
              onChange={(e) => void changeKnowledgeBase(e.target.value)}
              disabled={loadingConversation || Boolean(pending)}
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm sm:max-w-48 sm:flex-none dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="">None (general chat)</option>
              {knowledgeBases.data?.map((kb) => (
                <option key={kb.id} value={kb.id}>
                  {kb.name}
                </option>
              ))}
            </select>
          </label>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite">
          <div className="mx-auto max-w-3xl space-y-5">
            {settingsError && <ErrorAlert>{settingsError}</ErrorAlert>}
            {conversation.error ? (
              <ErrorAlert>
                {conversation.error.status === 404 ? 'This conversation does not exist or was deleted.' : conversation.error.message}
              </ErrorAlert>
            ) : loadingConversation ? (
              <p className="text-sm text-slate-500 dark:text-slate-400" role="status">
                Loading conversation…
              </p>
            ) : messageCount === 0 && !pending && !failed ? (
              <div className="py-12 text-center">
                <h2 className="text-xl font-semibold tracking-tight">How can I help?</h2>
                <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                  {kbName
                    ? `Answers will come from “${kbName}”, with citations.`
                    : 'No knowledge base selected: answers come from general knowledge.'}
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => {
                        setInput(suggestion)
                        inputRef.current?.focus()
                      }}
                      className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              detail?.messages.map((message) => <ChatMessageView key={message.id} message={message} />)
            )}

            {pending && (
              <>
                <UserBubble text={pending.text} images={previews(pending.images)} />
                <AssistantShell label="Assistant is responding">
                  {live.text ? (
                    // Not announced word by word (aria-live off); the finished answer is announced once.
                    // Formatted like the saved answer, so nothing jumps when it replaces this preview.
                    <div className="space-y-2 text-sm leading-relaxed" aria-live="off" data-testid="streaming-answer">
                      {renderAnswer(live.text, { spans: [], marker: () => null })}
                      <span className="inline-block h-4 w-1.5 animate-pulse rounded-sm bg-brand-500" aria-hidden />
                    </div>
                  ) : (
                    <p className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
                      <span className="flex gap-1" aria-hidden>
                        {[0, 150, 300].map((delay) => (
                          <span key={delay} className="size-1.5 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: `${delay}ms` }} />
                        ))}
                      </span>
                      {stageLabel(live, Boolean(selectedKb))}
                    </p>
                  )}
                </AssistantShell>
              </>
            )}

            {failed && (
              <UserBubble text={failed.text} images={previews(failed.images)}>
                <div role="alert" className="flex max-w-[85%] flex-wrap items-center justify-end gap-2 text-xs text-rose-600 dark:text-rose-400">
                  <span>Not sent: {failed.error.message}</span>
                  {failed.error.requestId && (
                    // Quote this when reporting a problem: every log line of the request carries it.
                    <span className="text-rose-500/80" title={`Request ID ${failed.error.requestId}`}>
                      Ref {failed.error.requestId.slice(0, 8)}
                    </span>
                  )}
                  <button type="button" onClick={() => void send(failed.text, failed.images)} className="font-semibold underline">
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInput(failed.text)
                      attachments.restore(failed.images)
                      setFailed(null)
                      inputRef.current?.focus()
                    }}
                    className="font-semibold underline"
                  >
                    Edit
                  </button>
                </div>
              </UserBubble>
            )}
            {stopped && (
              <p className="text-center text-xs text-slate-500 dark:text-slate-400" role="status">
                Stopped. Nothing was saved; your message is back in the box.
              </p>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          onDragOver={(event) => {
            if (!event.dataTransfer.types.includes('Files')) return
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            attachments.add(event.dataTransfer.files)
          }}
          className={`border-t border-slate-200 px-4 py-3 sm:px-6 dark:border-slate-800 ${dragging ? 'bg-brand-50 dark:bg-brand-500/10' : 'bg-white dark:bg-slate-900'}`}
        >
          {(attachments.attachments.length > 0 || attachments.notice || voice.status !== 'idle' || voice.error) && (
            <div className="mx-auto mb-2 max-w-3xl space-y-2">
              <RecordingBar
                status={voice.status}
                elapsed={voice.elapsed}
                maxSeconds={MAX_RECORDING_SECONDS}
                onStop={voice.stop}
                onCancel={voice.cancel}
              />
              {voice.error && (
                <p className="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
                  {voice.error}
                  <button type="button" onClick={voice.clearError} className="font-semibold underline">
                    Dismiss
                  </button>
                </p>
              )}
              <AttachmentChips attachments={attachments.attachments} onRemove={attachments.remove} />
              {attachments.notice && (
                <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
                  {attachments.notice}
                </p>
              )}
            </div>
          )}
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept={IMAGE_TYPES.join(',')}
              multiple
              className="sr-only"
              aria-label="Images to attach"
              tabIndex={-1}
              data-testid="image-input"
              onChange={(event) => {
                if (event.target.files?.length) attachments.add(event.target.files)
                event.target.value = ''
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Attach images"
              title="Attach images (or paste / drop them)"
              disabled={Boolean(conversation.error)}
              className="flex size-[42px] shrink-0 items-center justify-center rounded-xl border border-slate-300 text-slate-500 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800 dark:text-slate-400"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden>
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <circle cx="8.5" cy="10" r="1.5" />
                <path d="M21 16l-5-5-7 7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <MicButton status={voice.status} disabled={Boolean(conversation.error) || Boolean(pending)} onStart={() => void voice.start()} />
            <label htmlFor="chat-input" className="sr-only">
              Message
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              rows={1}
              value={input}
              maxLength={MAX_MESSAGE_LENGTH}
              onChange={(e) => {
                setInput(e.target.value)
                autoGrow(e.currentTarget)
              }}
              onKeyDown={handleKeyDown}
              onPaste={(event) => {
                const images = [...event.clipboardData.files].filter((file) => file.type.startsWith('image/'))
                if (images.length) {
                  event.preventDefault()
                  attachments.add(images)
                }
              }}
              // Short enough for one line on a phone; the header shows which knowledge base is used.
              placeholder={kbName ? 'Ask a question…' : 'Message the assistant…'}
              disabled={Boolean(conversation.error)}
              className="block max-h-[200px] min-h-[42px] w-full resize-none rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900"
            />
            {pending ? (
              <Button
                type="button"
                variant="secondary"
                onClick={() => activeStream.current?.cancel()}
                aria-label="Stop generating"
                className="h-[42px]"
              >
                Stop
              </Button>
            ) : (
              <Button
                type="submit"
                disabled={
                  (!input.trim() && attachments.readyIds.length === 0) || attachments.uploading || voice.status !== 'idle'
                }
                aria-label="Send message"
                className="h-[42px]"
              >
                Send
              </Button>
            )}
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-xs text-slate-500 dark:text-slate-400">
            Enter to send · Shift+Enter for a new line · Paste or drop images · Mic to dictate
            {input.length > MAX_MESSAGE_LENGTH - 200 && ` · ${MAX_MESSAGE_LENGTH - input.length} characters left`}
          </p>
        </form>
      </section>

      <ConfirmDialog
        open={deleting !== null}
        title="Delete conversation?"
        description={
          <>
            <strong>{deleting?.title}</strong> and all its messages will be permanently deleted.
          </>
        }
        confirmLabel="Delete conversation"
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return
          await deleteConversation(deleting.id)
          setConversations((prev) => prev?.filter((c) => c.id !== deleting.id) ?? null)
          toast.success('Conversation deleted.')
          if (deleting.id === conversationId) navigate('/chat', { replace: true })
        }}
      />
    </div>
  )
}
