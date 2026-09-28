import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { HEALTHY_BACKEND, json, mockFetch } from '../test/mockFetch'
import { SystemStatusCard } from './SystemStatusCard'

describe('SystemStatusCard', () => {
  it('shows all systems operational when every check passes', async () => {
    mockFetch(HEALTHY_BACKEND)

    render(<SystemStatusCard />)

    expect(await screen.findByText('All systems operational')).toBeInTheDocument()
    expect(screen.getByTestId('check-database')).toHaveTextContent('Operational')
    expect(screen.getByText(/v0\.1\.0 · test/)).toBeInTheDocument()
  })

  it('reports Redis and the number of ingestion workers', async () => {
    mockFetch({
      'GET /health': HEALTHY_BACKEND['GET /health'],
      'GET /health/ready': () =>
        json({
          status: 'ready',
          checks: { database: true, pgvector: true },
          services: { redis: true, ingestion_workers: 0, ingestion_waiting: 3 },
        }),
    })

    render(<SystemStatusCard />)

    expect(await screen.findByText('Some services are unavailable')).toBeInTheDocument()
    expect(screen.getByTestId('check-redis')).toHaveTextContent('Operational')
    expect(screen.getByTestId('check-workers')).toHaveTextContent('None running')
  })

  it('flags the database when readiness returns 503', async () => {
    mockFetch({
      'GET /health': HEALTHY_BACKEND['GET /health'],
      'GET /health/ready': () => json({ status: 'not_ready', checks: { database: false, pgvector: false } }, 503),
    })

    render(<SystemStatusCard />)

    expect(await screen.findByText('Some services are unavailable')).toBeInTheDocument()
    expect(screen.getByTestId('check-api')).toHaveTextContent('Operational')
    expect(screen.getByTestId('check-database')).toHaveTextContent('Unavailable')
  })

  it('shows a friendly error when the backend is unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))

    render(<SystemStatusCard />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to reach the server')
    expect(screen.getByTestId('check-api')).toHaveTextContent('Unavailable')
  })
})
