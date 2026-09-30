import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { closeChatSocket } from '../services/chatSocket'
import { tokenStorage } from '../services/tokenStorage'
import { HEALTHY_BACKEND, TEST_USER, errorEnvelope, json, mockFetch, tokenResponse } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({ ...HEALTHY_BACKEND, 'GET /auth/me': () => json(TEST_USER), ...extra })
}

function bodyOf(spy: ReturnType<typeof mockFetch>, method: string, path: string) {
  const call = spy.mock.calls.find(([input, init]) => init?.method === method && String(input).endsWith(path))!
  return JSON.parse(String(call[1]!.body))
}

afterEach(() => {
  closeChatSocket()
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('SettingsPage', () => {
  it('saves the profile name', async () => {
    const spy = backend({ 'PATCH /auth/me': () => json({ ...TEST_USER, full_name: 'Ada King' }) })
    renderSignedIn('/settings')

    const name = await screen.findByLabelText('Full name')
    expect(within(screen.getByRole('region', { name: 'Profile' })).getByText(TEST_USER.email)).toBeInTheDocument()
    await userEvent.clear(name)
    await userEvent.type(name, 'Ada King')
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    expect(await screen.findByText('Profile saved.')).toBeInTheDocument()
    expect(bodyOf(spy, 'PATCH', '/auth/me')).toEqual({ full_name: 'Ada King' })
    expect(screen.getByText('Ada King')).toBeInTheDocument() // the sidebar shows the new name
  })

  it('changes the password and keeps this session with the new token', async () => {
    const spy = backend({ 'POST /auth/change-password': () => json(tokenResponse('token-after-change')) })
    renderSignedIn('/settings')

    await userEvent.type(await screen.findByLabelText('Current password'), 'old-pass-1')
    await userEvent.type(screen.getByLabelText('New password'), 'new-pass-2')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-pass-2')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Password changed. You were signed out on your other devices.')).toBeInTheDocument()
    expect(bodyOf(spy, 'POST', '/auth/change-password')).toEqual({ current_password: 'old-pass-1', new_password: 'new-pass-2' })
    expect(tokenStorage.get()).toBe('token-after-change')
    expect(screen.getByLabelText('Current password')).toHaveValue('')
  })

  it('shows a wrong current password without signing out', async () => {
    backend({ 'POST /auth/change-password': () => errorEnvelope(400, 'wrong_password', 'Your current password is incorrect.') })
    renderSignedIn('/settings')

    await userEvent.type(await screen.findByLabelText('Current password'), 'typo-1')
    await userEvent.type(screen.getByLabelText('New password'), 'new-pass-2')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-pass-2')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Your current password is incorrect.')).toBeInTheDocument()
    expect(tokenStorage.get()).toBe('test-token')
  })

  it('will not submit mismatched passwords', async () => {
    backend()
    renderSignedIn('/settings')

    await userEvent.type(await screen.findByLabelText('Current password'), 'old-pass-1')
    await userEvent.type(screen.getByLabelText('New password'), 'new-pass-2')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-pass-3')

    expect(screen.getByText('The passwords do not match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toBeDisabled()
  })

  it('signs out other devices after confirmation', async () => {
    backend({ 'POST /auth/logout-all': () => json(tokenResponse('token-after-logout-all')) })
    renderSignedIn('/settings')

    await userEvent.click(await screen.findByRole('button', { name: 'Sign out of other devices' }))
    const dialog = screen.getByRole('dialog', { name: 'Sign out of all other devices?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Sign out other devices' }))

    expect(await screen.findByText('Signed out of all other devices.')).toBeInTheDocument()
    expect(tokenStorage.get()).toBe('token-after-logout-all')
  })

  it('switches the theme and remembers it', async () => {
    backend()
    renderSignedIn('/settings')

    expect(await screen.findByRole('radio', { name: /System/ })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: /Dark/ }))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('mindora-theme')).toBe('dark')

    await userEvent.click(screen.getByRole('radio', { name: /System/ }))
    expect(localStorage.getItem('mindora-theme')).toBeNull()
  })
})
