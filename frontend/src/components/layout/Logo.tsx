export function Logo({ inverted = false }: { inverted?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <span
        className={`flex size-8 items-center justify-center rounded-lg ${inverted ? 'bg-white/15 text-white' : 'bg-brand-600 text-white'}`}
        aria-hidden
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-4.5">
          <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" strokeLinejoin="round" />
          <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" strokeLinejoin="round" />
        </svg>
      </span>
      <span className={`text-sm font-semibold tracking-tight ${inverted ? 'text-white' : ''}`}>RAG Assistant</span>
    </span>
  )
}
