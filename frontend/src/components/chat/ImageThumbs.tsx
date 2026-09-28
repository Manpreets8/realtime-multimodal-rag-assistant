import { useImageUrl } from '../../hooks/useImageUrl'
import type { Attachment } from '../../hooks/useImageAttachments'

const THUMB = 'size-20 rounded-lg border border-slate-200 object-cover dark:border-slate-700'

/** A sent image, loaded with the session token; opens full size in a new tab. */
export function StoredImage({ id, filename }: { id: string; filename: string }) {
  const { url, failed } = useImageUrl(id)
  if (failed) {
    return (
      <span className={`${THUMB} flex items-center justify-center bg-slate-100 p-1 text-center text-[10px] text-slate-500 dark:bg-slate-800 dark:text-slate-400`}>
        Image unavailable
      </span>
    )
  }
  if (!url) return <span className={`${THUMB} animate-pulse bg-slate-200 dark:bg-slate-800`} aria-label={`Loading ${filename}`} />
  return (
    <a href={url} target="_blank" rel="noreferrer" title={filename}>
      <img src={url} alt={filename} className={THUMB} />
    </a>
  )
}

/** Composer chips for images being attached: preview, upload state, remove. */
export function AttachmentChips({ attachments, onRemove }: { attachments: Attachment[]; onRemove: (localId: string) => void }) {
  if (attachments.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Attached images">
      {attachments.map((attachment) => (
        <li key={attachment.localId} className="relative" title={attachment.error ?? attachment.file.name}>
          <img
            src={attachment.previewUrl}
            alt={attachment.file.name}
            className={`${THUMB} size-16 ${attachment.state === 'uploading' ? 'opacity-50' : ''} ${attachment.state === 'error' ? 'border-rose-400 opacity-60' : ''}`}
          />
          {attachment.state === 'uploading' && (
            <span className="absolute inset-0 flex items-center justify-center" role="status" aria-label={`Uploading ${attachment.file.name}`}>
              <span className="size-5 animate-spin rounded-full border-2 border-brand-600 border-r-transparent" />
            </span>
          )}
          {attachment.state === 'error' && (
            <span className="absolute inset-x-0 bottom-0 rounded-b-lg bg-rose-600/90 px-1 text-center text-[10px] text-white">Failed</span>
          )}
          <button
            type="button"
            onClick={() => onRemove(attachment.localId)}
            aria-label={`Remove ${attachment.file.name}`}
            className="absolute -top-2 -right-2 flex size-5 items-center justify-center rounded-full bg-slate-800 text-xs text-white hover:bg-slate-900"
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  )
}
