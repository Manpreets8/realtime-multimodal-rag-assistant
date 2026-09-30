import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useToast } from '../../hooks/useToast'
import { ToastProvider } from './Toast'

function Trigger() {
  const toast = useToast()
  return (
    <>
      <button onClick={() => toast.success('Saved.')}>ok</button>
      <button onClick={() => toast.error('Could not save.')}>fail</button>
    </>
  )
}

afterEach(() => vi.useRealTimers())

describe('Toasts', () => {
  it('announces confirmations politely and errors assertively', async () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'ok' }))
    await userEvent.click(screen.getByRole('button', { name: 'fail' }))

    expect(screen.getByRole('status')).toHaveTextContent('Saved.')
    expect(screen.getByRole('alert')).toHaveTextContent('Could not save.')
  })

  it('can be dismissed, and dismisses itself after a few seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

    await user.click(screen.getByRole('button', { name: 'ok' }))
    await user.click(screen.getAllByRole('button', { name: 'Dismiss notification' })[0])
    expect(screen.queryByText('Saved.')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'fail' }))
    act(() => vi.advanceTimersByTime(5100))
    expect(screen.queryByText('Could not save.')).not.toBeInTheDocument()
  })

  it('shows at most three at a time', async () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    )
    for (let i = 0; i < 5; i++) await userEvent.click(screen.getByRole('button', { name: 'ok' }))

    expect(screen.getAllByRole('status')).toHaveLength(3)
  })
})
