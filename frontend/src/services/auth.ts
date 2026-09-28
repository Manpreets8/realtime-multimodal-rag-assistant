import { apiRequest } from './api'

export interface User {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface RegisterInput {
  email: string
  password: string
  full_name?: string
}

export function register(input: RegisterInput): Promise<TokenResponse> {
  return apiRequest<TokenResponse>('/auth/register', { method: 'POST', json: input })
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return apiRequest<TokenResponse>('/auth/login', { method: 'POST', json: { email, password } })
}

export function logout(): Promise<null> {
  return apiRequest<null>('/auth/logout', { method: 'POST' })
}

export function getCurrentUser(): Promise<User> {
  return apiRequest<User>('/auth/me')
}
