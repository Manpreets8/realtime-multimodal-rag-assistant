import type { ChatMessage } from '../../services/chat'
import { AnswerBody } from '../citations/AnswerBody'
import { StoredImage } from './ImageThumbs'

function AssistantAvatar() {
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white" aria-hidden>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-4">
        <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

export function UserBubble({ text, images, children }: { text: string; images?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-end gap-1" role="article" aria-label="Your message">
      {images && <div className="flex max-w-[85%] flex-wrap justify-end gap-2">{images}</div>}
      {text && (
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-sm whitespace-pre-wrap text-white sm:max-w-[75%]">
          {text}
        </div>
      )}
      {children}
    </div>
  )
}

export function AssistantShell({ children, label = 'Assistant' }: { children: React.ReactNode; label?: string }) {
  return (
    <div className="flex gap-3" role="article" aria-label={label}>
      <AssistantAvatar />
      <div className="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
        {children}
      </div>
    </div>
  )
}

export function ChatMessageView({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    const images = message.images?.length
      ? message.images.map((image) => <StoredImage key={image.id} id={image.id} filename={image.filename} />)
      : undefined
    return <UserBubble text={message.content} images={images} />
  }
  return (
    <AssistantShell label="Assistant message">
      <AnswerBody
        text={message.content}
        answerType={message.answer_type}
        citations={message.citations}
        sources={message.sources}
        truncated={message.truncated}
        model={message.model}
        usage={message.usage}
        reranked={Boolean(message.retrieval?.reranked)}
        timingsMs={message.timings_ms}
      />
      {message.retrieval_query && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400" title="Follow-up rewritten into a standalone search query">
          Searched for: “{message.retrieval_query}”
        </p>
      )}
    </AssistantShell>
  )
}
