import { useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { AuthLayout } from '../components/auth/AuthLayout'
import { ErrorAlert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { TextField } from '../components/ui/TextField'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../services/api'

interface FormState {
  full_name: string
  email: string
  password: string
  confirm: string
}

type FieldErrors = Partial<Record<keyof FormState, string>>

// Mirrors the backend rules in app/schemas/auth.py; the server remains the source of truth.
function validate(form: FormState): FieldErrors {
  const errors: FieldErrors = {}
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) errors.email = 'Enter a valid email address.'
  if (form.password.length < 8) errors.password = 'Password must be at least 8 characters.'
  else if (form.password.length > 128) errors.password = 'Password must be at most 128 characters.'
  else if (!/[A-Za-z]/.test(form.password) || !/\d/.test(form.password))
    errors.password = 'Password must contain at least one letter and one number.'
  if (form.confirm !== form.password) errors.confirm = 'Passwords do not match.'
  return errors
}

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const [form, setForm] = useState<FormState>({ full_name: '', email: '', password: '', confirm: '' })
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [error, setError] = useState<ApiError | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const update = (field: keyof FormState) => (e: ChangeEvent<HTMLInputElement>) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }))
    setFieldErrors((prev) => ({ ...prev, [field]: undefined }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    const errors = validate(form)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    try {
      await register({ email: form.email.trim(), password: form.password, full_name: form.full_name.trim() || undefined })
      navigate('/', { replace: true })
    } catch (err) {
      const apiError = err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null)
      const serverFieldErrors = apiError.fieldErrors()
      if (Object.keys(serverFieldErrors).length > 0) setFieldErrors(serverFieldErrors)
      else setError(apiError)
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle={
        <>
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        {error && <ErrorAlert requestId={error.status >= 500 ? error.requestId : null}>{error.message}</ErrorAlert>}
        <TextField label="Full name (optional)" autoComplete="name" value={form.full_name} onChange={update('full_name')} error={fieldErrors.full_name} />
        <TextField label="Email" type="email" autoComplete="email" required value={form.email} onChange={update('email')} error={fieldErrors.email} />
        <TextField
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          value={form.password}
          onChange={update('password')}
          error={fieldErrors.password}
          hint="At least 8 characters, including a letter and a number."
        />
        <TextField label="Confirm password" type="password" autoComplete="new-password" required value={form.confirm} onChange={update('confirm')} error={fieldErrors.confirm} />
        <Button type="submit" loading={submitting} className="w-full">
          Create account
        </Button>
      </form>
    </AuthLayout>
  )
}
