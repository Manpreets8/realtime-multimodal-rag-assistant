/**
 * Access-token persistence.
 *
 * Trade-off: localStorage keeps the user signed in across tabs/reloads and works
 * for WebSocket auth later, but is readable by any script on the page, so the
 * app must never render untrusted HTML. Tokens are short-lived (ACCESS_TOKEN_EXPIRE_MINUTES)
 * and revoked server-side on logout.
 */

const TOKEN_KEY = 'rag.access_token'

export const tokenStorage = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, token)
    } catch {
      // Storage unavailable (private mode / blocked): session lasts for this page only.
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(TOKEN_KEY)
    } catch {
      // ignore
    }
  },
}
