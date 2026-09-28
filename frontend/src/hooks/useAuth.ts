import { use } from 'react'

import { AuthContext, type AuthContextValue } from '../contexts/authContext'

export function useAuth(): AuthContextValue {
  const context = use(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
