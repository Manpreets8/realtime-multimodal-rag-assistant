import { useState, type FormEvent } from 'react'

import { ApiError } from '../../services/api'
import type { KnowledgeBase, KnowledgeBaseInput } from '../../services/knowledgeBases'
import { ErrorAlert } from '../ui/Alert'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { TextAreaField } from '../ui/TextAreaField'
import { TextField } from '../ui/TextField'

interface KnowledgeBaseFormDialogProps {
  open: boolean
  /** When provided the dialog edits this knowledge base; otherwise it creates one. */
  initial?: Pick<KnowledgeBase, 'name' | 'description'>
  onSubmit: (input: KnowledgeBaseInput) => Promise<void>
  onClose: () => void
}

export function KnowledgeBaseFormDialog(props: KnowledgeBaseFormDialogProps) {
  // Remount the form each time the dialog opens so it starts from `initial`.
  return props.open ? <KnowledgeBaseForm {...props} /> : null
}

function KnowledgeBaseForm({ initial, onSubmit, onClose }: KnowledgeBaseFormDialogProps) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<ApiError | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const editing = Boolean(initial)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!name.trim()) {
      setFieldErrors({ name: 'Name is required.' })
      return
    }
    setSubmitting(true)
    setError(null)
    setFieldErrors({})
    try {
      await onSubmit({ name: name.trim(), description: description.trim() || null })
      onClose()
    } catch (err) {
      const apiError = err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null)
      const serverFieldErrors = apiError.fieldErrors()
      if (Object.keys(serverFieldErrors).length > 0) setFieldErrors(serverFieldErrors)
      else if (apiError.code === 'conflict') setFieldErrors({ name: apiError.message })
      else setError(apiError)
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open
      title={editing ? 'Edit knowledge base' : 'New knowledge base'}
      description={editing ? undefined : 'Group related documents so questions can be answered from them.'}
      onClose={onClose}
      dismissible={!submitting}
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error && <ErrorAlert>{error.message}</ErrorAlert>}
        <TextField
          label="Name"
          value={name}
          maxLength={100}
          placeholder="e.g. Company Policies"
          onChange={(e) => {
            setName(e.target.value)
            setFieldErrors((prev) => ({ ...prev, name: '' }))
          }}
          error={fieldErrors.name || undefined}
        />
        <TextAreaField
          label="Description (optional)"
          value={description}
          maxLength={1000}
          placeholder="What kind of documents will this contain?"
          onChange={(e) => setDescription(e.target.value)}
          error={fieldErrors.description || undefined}
        />
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            {editing ? 'Save changes' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
