import { apiRequest } from './api'

export interface Liveness {
  status: 'ok'
  app: string
  version: string
  environment: string
}

export interface Readiness {
  status: 'ready' | 'not_ready'
  checks: Record<string, boolean>
  /** Not part of readiness; the API degrades gracefully without them. */
  services?: { redis: boolean; ingestion_workers: number; ingestion_waiting: number } | null
}

export function getLiveness(): Promise<Liveness> {
  return apiRequest<Liveness>('/health')
}

export function getReadiness(): Promise<Readiness> {
  return apiRequest<Readiness>('/health/ready', { acceptStatuses: [503] })
}
