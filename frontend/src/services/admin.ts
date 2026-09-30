import { apiRequest } from './api'
import type { User, UserRole } from './auth'

export interface AdminUser extends User {
  knowledge_bases: number
  documents: number
  conversations: number
}

export interface AdminUserList {
  items: AdminUser[]
  total: number
}

export interface AdminUserUpdate {
  role?: UserRole
  is_active?: boolean
}

export function listUsers(params: { search?: string; limit: number; offset: number }): Promise<AdminUserList> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) })
  if (params.search) query.set('search', params.search)
  return apiRequest<AdminUserList>(`/admin/users?${query}`)
}

export function updateUser(id: string, update: AdminUserUpdate): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', json: update })
}
