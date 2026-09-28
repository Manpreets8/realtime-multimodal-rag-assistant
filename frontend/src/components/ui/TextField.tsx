import { useId, type InputHTMLAttributes } from 'react'

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
  hint?: string
}

export function TextField({ label, error, hint, className = '', ...props }: TextFieldProps) {
  const id = useId()
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined

  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`block w-full rounded-lg border bg-white px-3 py-2 text-sm shadow-xs outline-none transition placeholder:text-slate-400 focus:ring-2 dark:bg-slate-900 ${
          error
            ? 'border-rose-400 focus:border-rose-500 focus:ring-rose-500/20'
            : 'border-slate-300 focus:border-brand-500 focus:ring-brand-500/20 dark:border-slate-700'
        }`}
        {...props}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs text-rose-600 dark:text-rose-400">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
          {hint}
        </p>
      ) : null}
    </div>
  )
}
