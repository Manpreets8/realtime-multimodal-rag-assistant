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

  /** A new token for the current session (older tokens were invalidated): the chat socket
   * authenticated with the old one, so it is closed and reconnects with the new token. */
  const replaceToken = useCallback(
    (response: authApi.TokenResponse) => {
      closeChatSocket()
      startSession(response)
    },
    [startSession],
  )

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

  const updateProfile = useCallback(async (fullName: string | null) => {
    setUser(await authApi.updateProfile(fullName))
  }, [])

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) =>
      replaceToken(await authApi.changePassword(currentPassword, newPassword)),
    [replaceToken],
  )

  const signOutOtherSessions = useCallback(async () => replaceToken(await authApi.logoutAll()), [replaceToken])

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, login, register, logout, updateProfile, changePassword, signOutOtherSessions }),
    [status, user, login, register, logout, updateProfile, changePassword, signOutOtherSessions],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}
