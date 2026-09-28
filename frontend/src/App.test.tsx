import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import App from './App'
import { tokenStorage } from './services/tokenStorage'
import { HEALTHY_BACKEND, TEST_USER, errorEnvelope, json, mockFetch, tokenResponse } from './test/mockFetch'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('authentication flow', () => {
  it('redirects anonymous users from the dashboard to login', async () => {
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
  })

  it('logs in, stores the token and shows the dashboard', async () => {
    const fetchSpy = mockFetch({ ...HEALTHY_BACKEND, 'POST /auth/login': () => json(tokenResponse('token-login')) })
    renderAt('/login')

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'secret-pass-1')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: 'Welcome, Ada' })).toBeInTheDocument()
    expect(tokenStorage.get()).toBe('token-login')
    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/auth/login'))!
    expect(JSON.parse(String(init?.body))).toEqual({ email: 'ada@example.com', password: 'secret-pass-1' })
  })

  it('shows the server message for invalid credentials', async () => {
    mockFetch({ 'POST /auth/login': () => errorEnvelope(401, 'unauthorized', 'Invalid email or password.') })
    renderAt('/login')

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(tokenStorage.get()).toBeNull()
  })

  it('validates the registration form before calling the API', async () => {
    const fetchSpy = mockFetch({})
    renderAt('/register')

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'lettersonly')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'different1')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(screen.getByText('Password must contain at least one letter and one number.')).toBeInTheDocument()
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('registers a new account and lands on the dashboard', async () => {
    mockFetch({ ...HEALTHY_BACKEND, 'POST /auth/register': () => json(tokenResponse('token-new'), 201) })
    renderAt('/register')

    await userEvent.type(screen.getByLabelText('Full name (optional)'), 'Ada Lovelace')
    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'analytical1')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'analytical1')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: 'Welcome, Ada' })).toBeInTheDocument()
    expect(tokenStorage.get()).toBe('token-new')
  })

  it('shows a conflict error when the email is already registered', async () => {
    mockFetch({
      'POST /auth/register': () => errorEnvelope(409, 'conflict', 'An account with this email already exists.'),
    })
    renderAt('/register')

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'analytical1')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'analytical1')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An account with this email already exists.')
  })

  it('restores the session from a stored token', async () => {
    tokenStorage.set('stored-token')
    const fetchSpy = mockFetch({ ...HEALTHY_BACKEND, 'GET /auth/me': () => json(TEST_USER) })
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Welcome, Ada' })).toBeInTheDocument()
    const [, init] = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/auth/me'))!
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer stored-token')
  })

  it('drops an expired stored token and returns to login', async () => {
    tokenStorage.set('expired-token')
    mockFetch({ 'GET /auth/me': () => errorEnvelope(401, 'unauthorized', 'Your session has expired.') })
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
    expect(tokenStorage.get()).toBeNull()
  })

  it('logs out: revokes the token server-side and clears the session', async () => {
    tokenStorage.set('session-token')
    const fetchSpy = mockFetch({
      ...HEALTHY_BACKEND,
      'GET /auth/me': () => json(TEST_USER),
      'POST /auth/logout': () => json(null, 204),
    })
    renderAt('/')

    await userEvent.click(await screen.findByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
    expect(tokenStorage.get()).toBeNull()
    expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/auth/logout'))).toBe(true)
  })

  it('sends signed-in users away from the login page', async () => {
    tokenStorage.set('session-token')
    mockFetch({ ...HEALTHY_BACKEND, 'GET /auth/me': () => json(TEST_USER) })
    renderAt('/login')

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Welcome, Ada' })).toBeInTheDocument())
  })

  it('shows a not-found page for unknown addresses', async () => {
    renderAt('/does/not/exist')

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to dashboard' })).toHaveAttribute('href', '/')
  })
})
