import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'

function Broken({ fail }: { fail: boolean }) {
  if (fail) throw new Error('render failure with internal details')
  return <p>Page content</p>
}

describe('ErrorBoundary', () => {
  it('shows a recovery screen instead of a blank page, without internal details', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary resetKey="/chat">
        <Broken fail />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to dashboard' })).toHaveAttribute('href', '/')
    expect(screen.queryByText(/internal details/)).not.toBeInTheDocument()
    expect(consoleError).toHaveBeenCalled() // details go to the console for debugging
  })

  it('recovers when the user navigates to another page', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <ErrorBoundary resetKey="/chat">
        <Broken fail />
      </ErrorBoundary>,
    )

    rerender(
      <ErrorBoundary resetKey="/knowledge-bases">
        <Broken fail={false} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Page content')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
