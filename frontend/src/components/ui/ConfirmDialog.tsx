import { useState, type ReactNode } from 'react'

import { ApiError } from '../../services/api'
import { ErrorAlert } from './Alert'
import { Button } from './Button'
import { Modal } from './Modal'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description: ReactNode
  confirmLabel: string
  onConfirm: () => Promise<void>
  onClose: () => void
}

/** Destructive-action confirmation. Stays open and shows the error if the action fails. */
export function ConfirmDialog({ open, title, description, confirmLabel, onConfirm, onClose }: ConfirmDialogProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setError(null)
    onClose()
  }

  async function confirm() {
    setPending(true)
    setError(null)
    try {
      await onConfirm()
      close()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setPending(false)
    }
  }

  return (
    <Modal open={open} title={title} description={description} onClose={close} dismissible={!pending}>
      {error && (
        <div className="mb-4">
          <ErrorAlert>{error}</ErrorAlert>
        </div>
      )}
      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={close} disabled={pending}>
          Cancel
        </Button>
        <Button
          onClick={confirm}
          loading={pending}
          className="bg-rose-600 hover:bg-rose-700 focus-visible:outline-rose-600"
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  )
}
