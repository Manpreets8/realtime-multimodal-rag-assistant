import { useCallback, useState, type FormEvent } from 'react'

import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { Skeleton } from '../components/ui/Skeleton'
import { useAuth } from '../hooks/useAuth'
import { useResource } from '../hooks/useResource'
import { useToast } from '../hooks/useToast'
import { listUsers, updateUser, type AdminUser, type AdminUserUpdate } from '../services/admin'
import { ApiError } from '../services/api'
import type { UserRole } from '../services/auth'
import { formatDateTime } from '../utils/format'

const PAGE_SIZE = 25
const GRID = 'md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1.5fr)_minmax(0,1.1fr)]'
// Labels each field on phones; on wide screens the column headings do.
const MOBILE_LABEL = 'text-xs font-medium text-slate-500 md:hidden dark:text-slate-400'

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
  const toast = useToast()
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
    const who = saved.full_name || saved.email
    if (update.role) toast.success(`${who} is now ${saved.role === 'admin' ? 'an administrator' : 'a regular user'}.`)
    if (update.is_active !== undefined) toast.success(`${who} was ${saved.is_active ? 'enabled' : 'disabled'}.`)
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

      {/* One list for every screen size: columns on wide screens, stacked cards on phones. */}
      <section aria-label="Accounts" aria-busy={loading} className="mt-4 rounded-xl border border-slate-200 bg-white text-sm dark:border-slate-800 dark:bg-slate-900">
        <div aria-hidden className={`${GRID} hidden border-b border-slate-200 px-4 py-3 text-xs font-medium text-slate-500 uppercase md:grid dark:border-slate-800 dark:text-slate-400`}>
          <span>Account</span>
          <span>Role</span>
          <span>Status</span>
          <span>Activity</span>
          <span>Joined</span>
        </div>
        {loading && !data ? (
          <div className="space-y-3 p-4">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </div>
        ) : data && rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-slate-500 dark:text-slate-400">No accounts match “{search}”.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((row) => {
              const isMe = row.id === me?.id
              const busy = savingId === row.id
              return (
                <li key={row.id} className={`${GRID} grid gap-3 px-4 py-4 md:items-center md:gap-4 md:py-3`}>
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {row.full_name || row.email}
                      {isMe && (
                        <span className="ml-2 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-100">
                          You
                        </span>
                      )}
                    </p>
                    {row.full_name && <p className="truncate text-xs text-slate-500 dark:text-slate-400">{row.email}</p>}
                  </div>
                  <div className="flex items-center justify-between gap-3 md:block">
                    <span className={MOBILE_LABEL}>Role</span>
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
                  </div>
                  <div className="flex items-center justify-between gap-3 md:justify-start">
                    <span className={MOBILE_LABEL}>Status</span>
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
                  </div>
                  <div className="flex items-center justify-between gap-3 text-slate-600 md:block dark:text-slate-400">
                    <span className={MOBILE_LABEL}>Activity</span>
                    <span>
                      {row.knowledge_bases} KB · {row.documents} docs · {row.conversations} chats
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3 text-slate-600 md:block dark:text-slate-400">
                    <span className={MOBILE_LABEL}>Joined</span>
                    <span>{formatDateTime(row.created_at)}</span>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
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
