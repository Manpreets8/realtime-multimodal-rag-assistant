import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { setUnauthorizedHandler } from '../services/api'
import { closeChatSocket } from '../services/chatSocket'
import * as authApi from '../services/auth'
import { tokenStorage } from '../services/tokenStorage'
import { AuthContext, type AuthContextValue, type AuthStatus } from './authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<authApi.User | null>(null)
  const [status, setStatus] = useState<AuthStatus>(() => (tokenStorage.get() ? 'loading' : 'anonymous'))

  const clearSession = useCallback(() => {
    tokenStorage.clear()
    closeChatSocket()
    setUser(null)
    setStatus('anonymous')
  }, [])

  const startSession = useCallback((response: authApi.TokenResponse) => {
    tokenStorage.set(response.access_token)
    setUser(response.user)
    setStatus('authenticated')
  }, [])

  // Any 401 on an authenticated request (expired/revoked token) ends the session.
  useEffect(() => {
    setUnauthorizedHandler(clearSession)
    return () => setUnauthorizedHandler(null)
  }, [clearSession])

  // Restore the session from a stored token on first load.
  useEffect(() => {
    if (!tokenStorage.get()) return
    let cancelled = false
    authApi
      .getCurrentUser()
      .then((me) => {
        if (cancelled) return
        setUser(me)
        setStatus('authenticated')
      })
      .catch(() => {
        if (!cancelled) clearSession()
      })
    return () => {
      cancelled = true
    }
  }, [clearSession])

  const login = useCallback(
    async (email: string, password: string) => startSession(await authApi.login(email, password)),
    [startSession],
  )

  const register = useCallback(
    async (input: authApi.RegisterInput) => startSession(await authApi.register(input)),
    [startSession],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // The token may already be expired/revoked; the local session ends regardless.
    } finally {
      clearSession()
    }
  }, [clearSession])

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, login, register, logout }),
    [status, user, login, register, logout],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}
