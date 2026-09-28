import { useRef, useState, type DragEvent } from 'react'

import type { UploadItem } from '../../hooks/useDocumentUploads'
import type { UploadConfig } from '../../services/documents'
import { formatBytes } from '../../utils/format'

interface UploadDropzoneProps {
  config: UploadConfig | null
  items: UploadItem[]
  onFiles: (files: File[]) => void
  onDismiss: (id: string) => void
}

export function UploadDropzone({ config, items, onFiles, onDismiss }: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const accept = config?.supported_types.map((type) => type.extension).join(',')
  const labels = config ? [...new Set(config.supported_types.map((type) => type.label))].join(', ') : 'PDF, Word, Text, Markdown'

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (event.dataTransfer.files.length > 0) onFiles([...event.dataTransfer.files])
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging
            ? 'border-brand-500 bg-brand-50 dark:bg-brand-500/10'
            : 'border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900'
        }`}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="size-8 text-slate-500 dark:text-slate-400" aria-hidden>
          <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <p className="mt-3 text-sm">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-300"
          >
            Choose files
          </button>{' '}
          or drag and drop
        </p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {labels}
          {config && ` · up to ${formatBytes(config.max_file_size)} each`}
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={accept}
          className="sr-only"
          aria-label="Documents to upload"
          tabIndex={-1}
          data-testid="file-input"
          onChange={(event) => {
            if (event.target.files?.length) onFiles([...event.target.files])
            event.target.value = '' // allow re-selecting the same file after an error
          }}
        />
      </div>

      {items.length > 0 && (
        <ul className="space-y-2" aria-label="Uploads">
          {items.map((item) => (
            <li
              key={item.id}
              className="rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{item.file.name}</p>
                  <p className={`text-xs ${item.state === 'error' ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500'}`}>
                    {item.state === 'error'
                      ? item.error
                      : item.state === 'queued'
                        ? 'Waiting…'
                        : `Uploading ${Math.round(item.progress * 100)}% of ${formatBytes(item.file.size)}`}
                  </p>
                </div>
                {item.state === 'error' && (
                  <button
                    type="button"
                    onClick={() => onDismiss(item.id)}
                    className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 dark:text-slate-400"
                  >
                    Dismiss
                  </button>
                )}
              </div>
              {item.state === 'uploading' && (
                <div
                  className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"
                  role="progressbar"
                  aria-label={`Uploading ${item.file.name}`}
                  aria-valuenow={Math.round(item.progress * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div className="h-full rounded-full bg-brand-600 transition-[width]" style={{ width: `${item.progress * 100}%` }} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
