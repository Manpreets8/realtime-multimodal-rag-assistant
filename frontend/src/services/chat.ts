import { LONG_TIMEOUT_MS, apiRequest } from './api'
import type { AnswerQuote } from './rag'

export interface ChatSource {
  number: number
  chunk_id: string | null
  document_id: string | null
  knowledge_base_id: string | null
  filename: string
  page_number: number | null
  section: string | null
  content: string
  rerank_score: number | null
  similarity: number | null
}

export interface ChatCitation {
  source_number: number
  document_id: string | null
  filename: string
  page_number: number | null
  section: string | null
  quotes: AnswerQuote[]
  answer_spans: [number, number][]
}

export interface ImageRef {
  id: string
  filename: string
  media_type: string
  width: number
  height: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  images?: ImageRef[]
  answer_type: string | null
  grounded: boolean
  knowledge_base_id: string | null
  retrieval_query: string | null
  model: string | null
  usage: { input_tokens: number; output_tokens: number } | null
  truncated: boolean
  timings_ms: Record<string, number> | null
  retrieval: { reranked?: boolean; rewritten?: boolean } & Record<string, unknown> | null
  citations: ChatCitation[]
  sources: ChatSource[]
}

export interface ConversationSummary {
  id: string
  title: string
  knowledge_base_id: string | null
  knowledge_base_name: string | null
  message_count: number
  last_message_preview: string | null
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[]
}

export interface ChatResponse {
  conversation: ConversationSummary
  user_message: ChatMessage
  assistant_message: ChatMessage
}

export interface SendMessageInput {
  message: string
  conversationId?: string | null
  /** Only sent when defined: `null` switches to general chat, a string switches knowledge base. */
  knowledgeBaseId?: string | null
  /** IDs from POST /images, sent with this message. */
  imageIds?: string[]
}

/** The request body shared by `POST /chat` and the chat WebSocket. */
export function chatRequestBody({ message, conversationId, knowledgeBaseId, imageIds }: SendMessageInput): Record<string, unknown> {
  const body: Record<string, unknown> = { message }
  if (imageIds?.length) body.image_ids = imageIds
  if (conversationId) body.conversation_id = conversationId
  if (knowledgeBaseId !== undefined) body.knowledge_base_id = knowledgeBaseId
  return body
}

export function sendMessage(input: SendMessageInput): Promise<ChatResponse> {
  return apiRequest<ChatResponse>('/chat', { method: 'POST', json: chatRequestBody(input), timeoutMs: LONG_TIMEOUT_MS })
}

export function listConversations(): Promise<ConversationSummary[]> {
  return apiRequest<ConversationSummary[]>('/conversations')
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(`/conversations/${encodeURIComponent(id)}`)
}

export function updateConversation(
  id: string,
  changes: { title?: string; knowledge_base_id?: string | null },
): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>(`/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', json: changes })
}

export function deleteConversation(id: string): Promise<null> {
  return apiRequest<null>(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
