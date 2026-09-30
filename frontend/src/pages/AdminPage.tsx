import { useCallback, useState, type FormEvent } from 'react'

import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { useAuth } from '../hooks/useAuth'
import { useResource } from '../hooks/useResource'
import { listUsers, updateUser, type AdminUser, type AdminUserUpdate } from '../services/admin'
import { ApiError } from '../services/api'
import type { UserRole } from '../services/auth'
import { formatDateTime } from '../utils/format'

const PAGE_SIZE = 25

interface PendingChange {
  user: AdminUser
  update: AdminUserUpdate
  title: string
  description: string
  confirmLabel: string
}

function StatusBadge({ active }: { active: boolean }) {
  return active ? (
    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
      Active
    </span>
  ) : (
    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
      Disabled
    </span>
  )
}

export default function AdminPage() {
  const { user: me } = useAuth()
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [pending, setPending] = useState<PendingChange | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const fetchUsers = useCallback(() => listUsers({ search, limit: PAGE_SIZE, offset }), [search, offset])
  const { data, error, loading, setData } = useResource(fetchUsers)

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setSearch(query.trim())
  }

  async function apply(target: AdminUser, update: AdminUserUpdate) {
    const saved = await updateUser(target.id, update)
    setData((current) =>
      current ? { ...current, items: current.items.map((row) => (row.id === saved.id ? saved : row)) } : current,
    )
  }

  /** Changes that give or take away access are confirmed first; the reverse applies at once. */
  async function change(target: AdminUser, update: AdminUserUpdate) {
    setActionError(null)
    const label = target.full_name || target.email
    if (update.role === 'admin') {
      setPending({
        user: target,
        update,
        title: 'Make this account an administrator?',
        description: `${label} will be able to manage every account, including disabling other administrators.`,
        confirmLabel: 'Make administrator',
      })
      return
    }
    if (update.is_active === false) {
      setPending({
        user: target,
        update,
        title: 'Disable this account?',
        description: `${label} will be signed out at their next request and cannot sign in until the account is enabled again. Their data is kept.`,
        confirmLabel: 'Disable account',
      })
      return
    }
    setSavingId(target.id)
    try {
      await apply(target, update)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'The change could not be saved.')
    } finally {
      setSavingId(null)
    }
  }

  const total = data?.total ?? 0
  const rows = data?.items ?? []

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Manage accounts. Administrators see account details and activity counts, never the content of anyone&apos;s
          documents or conversations.
        </p>
      </header>

      <form onSubmit={submitSearch} role="search" className="mt-6 flex max-w-md gap-2">
        <label htmlFor="admin-search" className="sr-only">
          Search accounts
        </label>
        <input
          id="admin-search"
          type="search"
          value={query}
          maxLength={320}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search by email or name"
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:placeholder:text-slate-400"
        />
        <Button type="submit" variant="secondary">
          Search
        </Button>
      </form>

      <div className="mt-4 space-y-3">
        {error && <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>}
        {actionError && <ErrorAlert>{actionError}</ErrorAlert>}
      </div>

      <section aria-label="Accounts" className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-slate-200 text-xs text-slate-500 uppercase dark:border-slate-800 dark:text-slate-400">
            <tr>
              <th scope="col" className="px-4 py-3 font-medium">Account</th>
              <th scope="col" className="px-4 py-3 font-medium">Role</th>
              <th scope="col" className="px-4 py-3 font-medium">Status</th>
              <th scope="col" className="px-4 py-3 font-medium">Activity</th>
              <th scope="col" className="px-4 py-3 font-medium">Joined</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && !data
              ? Array.from({ length: 3 }, (_, index) => (
                  <tr key={index} aria-hidden>
                    <td colSpan={5} className="px-4 py-4">
                      <div className="h-4 w-2/3 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
                    </td>
                  </tr>
                ))
              : rows.map((row) => {
                  const isMe = row.id === me?.id
                  const busy = savingId === row.id
                  return (
                    <tr key={row.id}>
                      <td className="px-4 py-3">
                        <p className="font-medium">
                          {row.full_name || row.email}
                          {isMe && (
                            <span className="ml-2 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-100">
                              You
                            </span>
                          )}
                        </p>
                        {row.full_name && <p className="text-xs text-slate-500 dark:text-slate-400">{row.email}</p>}
                      </td>
                      <td className="px-4 py-3">
                        <select
                          aria-label={`Role for ${row.email}`}
                          value={row.role}
                          disabled={isMe || busy}
                          title={isMe ? "You can't change your own role" : undefined}
                          onChange={(event) => void change(row, { role: event.target.value as UserRole })}
                          className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900"
                        >
                          <option value="user">User</option>
                          <option value="admin">Administrator</option>
                        </select>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <StatusBadge active={row.is_active} />
                          {!isMe && (
                            <Button
                              variant="ghost"
                              className="px-2 py-1 text-xs"
                              disabled={busy}
                              onClick={() => void change(row, { is_active: !row.is_active })}
                              aria-label={`${row.is_active ? 'Disable' : 'Enable'} ${row.email}`}
                            >
                              {row.is_active ? 'Disable' : 'Enable'}
                            </Button>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                        {row.knowledge_bases} KB · {row.documents} docs · {row.conversations} chats
                      </td>
                      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">{formatDateTime(row.created_at)}</td>
                    </tr>
                  )
                })}
            {data && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500 dark:text-slate-400">
                  No accounts match “{search}”.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {total > 0 && (
        <nav aria-label="Pagination" className="mt-4 flex items-center justify-between text-sm text-slate-600 dark:text-slate-400">
          <p>
            Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </p>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}>
              Previous
            </Button>
            <Button variant="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </nav>
      )}

      <ConfirmDialog
        open={pending !== null}
        title={pending?.title ?? ''}
        description={pending?.description ?? ''}
        confirmLabel={pending?.confirmLabel ?? ''}
        onConfirm={async () => {
          if (pending) await apply(pending.user, pending.update)
        }}
        onClose={() => setPending(null)}
      />
    </div>
  )
}
