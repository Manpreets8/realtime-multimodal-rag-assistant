import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TEST_USER, errorEnvelope, json, mockFetch } from '../test/mockFetch'
import { renderSignedIn } from '../test/renderApp'

const ADMIN = { ...TEST_USER, role: 'admin' as const }
const counts = { knowledge_bases: 2, documents: 5, conversations: 7 }
const ME_ROW = { ...ADMIN, ...counts }
const BOB = {
  id: 'user-bob',
  email: 'bob@example.com',
  full_name: 'Bob Stone',
  role: 'user' as const,
  is_active: true,
  created_at: '2026-09-28T09:00:00Z',
  ...counts,
}

function backend(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    'GET /auth/me': () => json(ADMIN),
    'GET /admin/users': () => json({ items: [ME_ROW, BOB], total: 2 }),
    ...extra,
  })
}

function requests(spy: ReturnType<typeof mockFetch>, method: string, path: string) {
  return spy.mock.calls.filter(
    ([input, init]) => (init?.method ?? 'GET') === method && String(input).includes(path),
  )
}

describe('AdminPage', () => {
  it('lists accounts with activity counts, and the admin nav item', async () => {
    backend()
    renderSignedIn('/admin')

    const bob = (await screen.findByText('Bob Stone')).closest('tr')!
    expect(within(bob).getByText('bob@example.com')).toBeInTheDocument()
    expect(within(bob).getByText('2 KB · 5 docs · 7 chats')).toBeInTheDocument()
    expect(within(bob).getByText('Active')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/admin')
  })

  it('never offers to change your own account', async () => {
    backend()
    renderSignedIn('/admin')

    const me = (await screen.findByText('You')).closest('tr')!
    expect(within(me).getByRole('combobox', { name: `Role for ${ADMIN.email}` })).toBeDisabled()
    expect(within(me).queryByRole('button', { name: /Disable/ })).not.toBeInTheDocument()
  })

  it('searches by email or name', async () => {
    const spy = backend()
    renderSignedIn('/admin')
    await screen.findByText('Bob Stone')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search accounts' }), 'bob')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findByRole('table')
    expect(requests(spy, 'GET', '/admin/users?').at(-1)![0]).toContain('search=bob')
  })

  it('confirms before disabling an account', async () => {
    const spy = backend({ 'PATCH /admin/users/user-bob': () => json({ ...BOB, is_active: false }) })
    renderSignedIn('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Disable bob@example.com' }))
    const dialog = screen.getByRole('dialog', { name: 'Disable this account?' })
    expect(requests(spy, 'PATCH', '/admin/users/')).toHaveLength(0)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Disable account' }))

    const bob = (await screen.findByText('Disabled')).closest('tr')!
    expect(within(bob).getByRole('button', { name: 'Enable bob@example.com' })).toBeInTheDocument()
    expect(JSON.parse(String(requests(spy, 'PATCH', '/admin/users/user-bob')[0][1]!.body))).toEqual({
      is_active: false,
    })
  })

  it('confirms promotions and shows a failure in the dialog', async () => {
    backend({
      'PATCH /admin/users/user-bob': () => errorEnvelope(409, 'conflict', 'Something changed. Reload the page.'),
    })
    renderSignedIn('/admin')

    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Role for bob@example.com' }), 'admin')
    const dialog = screen.getByRole('dialog', { name: 'Make this account an administrator?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Make administrator' }))

    expect(await within(dialog).findByText('Something changed. Reload the page.')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Role for bob@example.com' })).toHaveValue('user')
  })

  it('is closed to regular users', async () => {
    mockFetch({ 'GET /auth/me': () => json(TEST_USER) })
    renderSignedIn('/admin')

    expect(await screen.findByRole('heading', { name: 'Administrators only' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument()
  })
})
