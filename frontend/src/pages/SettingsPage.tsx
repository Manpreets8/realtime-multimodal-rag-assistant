import { useState, type FormEvent, type ReactNode } from 'react'

import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'
import { TextField } from '../components/ui/TextField'
import { useAuth } from '../hooks/useAuth'
import { useToast } from '../hooks/useToast'
import { ApiError } from '../services/api'
import { getThemeChoice, setThemeChoice, type ThemeChoice } from '../services/theme'

const CARD = 'rounded-xl border border-slate-200 bg-white p-5 shadow-xs sm:p-6 dark:border-slate-800 dark:bg-slate-900'

function Section({ id, title, description, children }: { id: string; title: string; description: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className={CARD}>
      <h2 id={id} className="text-base font-semibold">
        {title}
      </h2>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{description}</p>
      <div className="mt-5">{children}</div>
    </section>
  )
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function ProfileSection() {
  const { user, updateProfile } = useAuth()
  const toast = useToast()
  const [name, setName] = useState(user?.full_name ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await updateProfile(name.trim() || null)
      toast.success('Profile saved.')
    } catch (err) {
      setError(messageOf(err, 'Your profile could not be saved.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section id="profile-heading" title="Profile" description="How your name appears in Mindora AI.">
      <form onSubmit={save} className="space-y-4">
        <TextField label="Full name" value={name} maxLength={120} autoComplete="name" onChange={(e) => setName(e.target.value)} />
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="font-medium text-slate-700 dark:text-slate-300">Email</dt>
            <dd className="mt-1 text-slate-600 dark:text-slate-400">{user?.email}</dd>
          </div>
          <div>
            <dt className="font-medium text-slate-700 dark:text-slate-300">Role</dt>
            <dd className="mt-1 text-slate-600 dark:text-slate-400">{user?.role === 'admin' ? 'Administrator' : 'User'}</dd>
          </div>
        </dl>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <Button type="submit" loading={saving} disabled={name.trim() === (user?.full_name ?? '')}>
          Save profile
        </Button>
      </form>
    </Section>
  )
}

const THEMES: { value: ThemeChoice; label: string; hint: string }[] = [
  { value: 'system', label: 'System', hint: 'Match your device' },
  { value: 'light', label: 'Light', hint: 'Always light' },
  { value: 'dark', label: 'Dark', hint: 'Always dark' },
]

function AppearanceSection() {
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice)

  function choose(value: ThemeChoice) {
    setTheme(value)
    setThemeChoice(value)
  }

  return (
    <Section id="appearance-heading" title="Appearance" description="Saved in this browser.">
      <fieldset>
        <legend className="sr-only">Theme</legend>
        <div className="grid gap-3 sm:grid-cols-3">
          {THEMES.map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-brand-500/40 ${
                theme === option.value
                  ? 'border-brand-600 bg-brand-50 dark:border-brand-400 dark:bg-brand-500/10'
                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800'
              }`}
            >
              <input
                type="radio"
                name="theme"
                value={option.value}
                checked={theme === option.value}
                onChange={() => choose(option.value)}
                className="mt-0.5 accent-brand-600"
              />
              <span>
                <span className="block font-medium">{option.label}</span>
                <span className="block text-xs text-slate-600 dark:text-slate-400">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </Section>
  )
}

function PasswordSection() {
  const { changePassword } = useAuth()
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mismatch = confirm.length > 0 && next !== confirm

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (next !== confirm) return
    setSaving(true)
    setError(null)
    try {
      await changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      toast.success('Password changed. You were signed out on your other devices.')
    } catch (err) {
      setError(messageOf(err, 'Your password could not be changed.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section
      id="password-heading"
      title="Password"
      description="At least 8 characters, with a letter and a number. Changing it signs you out on your other devices."
    >
      <form onSubmit={save} className="grid gap-4 sm:max-w-md">
        <TextField label="Current password" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <TextField label="New password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />
        <TextField
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          error={mismatch ? 'The passwords do not match.' : undefined}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <div>
          <Button type="submit" loading={saving} disabled={!current || !next || mismatch}>
            Change password
          </Button>
        </div>
      </form>
    </Section>
  )
}

function SessionsSection() {
  const { signOutOtherSessions } = useAuth()
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)

  return (
    <Section
      id="sessions-heading"
      title="Sessions"
      description="Signed in on a device you no longer use, or a shared computer? Sign out everywhere except here."
    >
      <Button variant="secondary" onClick={() => setConfirming(true)}>
        Sign out of other devices
      </Button>
      <ConfirmDialog
        open={confirming}
        title="Sign out of all other devices?"
        description="Every other browser and device signed in to your account will need to sign in again. You stay signed in here."
        confirmLabel="Sign out other devices"
        onConfirm={async () => {
          await signOutOtherSessions()
          toast.success('Signed out of all other devices.')
        }}
        onClose={() => setConfirming(false)}
      />
    </Section>
  )
}

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-8 sm:py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">Your profile, appearance and account security.</p>
      </header>
      <div className="mt-8 space-y-6">
        <ProfileSection />
        <AppearanceSection />
        <PasswordSection />
        <SessionsSection />
      </div>
    </div>
  )
}
