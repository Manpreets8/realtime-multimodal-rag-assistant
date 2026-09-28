import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, type Location } from 'react-router'

import { AuthLayout } from '../components/auth/AuthLayout'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { TextField } from '../components/ui/TextField'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../services/api'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: Location } | null)?.from?.pathname ?? '/'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null))
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle={
        <>
          New here?{' '}
          <Link to="/register" className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        {error && <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>}
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Button type="submit" loading={submitting} disabled={!email || !password} className="w-full">
          Sign in
        </Button>
      </form>
    </AuthLayout>
  )
}
