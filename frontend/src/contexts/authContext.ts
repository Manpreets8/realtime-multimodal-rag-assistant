import { createContext } from 'react'

import type { RegisterInput, User } from '../services/auth'

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

export interface AuthContextValue {
  status: AuthStatus
  user: User | null
  login: (email: string, password: string) => Promise<void>
  register: (input: RegisterInput) => Promise<void>
  logout: () => Promise<void>
  updateProfile: (fullName: string | null) => Promise<void>
  /** Other sessions are signed out; this one continues with a new token. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  signOutOtherSessions: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
