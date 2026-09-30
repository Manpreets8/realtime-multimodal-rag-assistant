/** The knowledge base last selected in chat, so a new chat starts with it (per browser). */

const KEY = 'mindora-last-kb'

export const lastKnowledgeBase = {
  get(): string | null {
    try {
      return localStorage.getItem(KEY)
    } catch {
      return null // storage blocked: start new chats as general chats
    }
  },
  set(id: string | null) {
    try {
      if (id) localStorage.setItem(KEY, id)
      else localStorage.removeItem(KEY)
    } catch {
      // Not remembered; nothing else depends on it.
    }
  },
}
