import { useResource } from '../hooks/useResource'
import { useSystemStatus } from '../hooks/useSystemStatus'
import { getProviders, type ProviderStatus } from '../services/dashboard'

type CheckState = 'ok' | 'fail' | 'pending' | 'off'

const CAPABILITIES: Record<ProviderStatus['capability'], string> = {
  llm: 'Answers',
  vision: 'Image understanding',
  embeddings: 'Embeddings',
  reranking: 'Reranking',
  speech_to_text: 'Speech to text',
  text_to_speech: 'Text to speech',
}

function providerState(provider: ProviderStatus): { state: CheckState; label: string } {
  if (provider.runs === 'off') return { state: 'off', label: 'Off' }
  if (!provider.configured) return { state: 'fail', label: 'Not configured' }
  return { state: 'ok', label: provider.runs === 'local' ? 'Local' : 'API' }
}

/** Which provider and model serves each AI capability (GET /system/providers). */
function AIServices() {
  const { data } = useResource(getProviders)
  if (!data) return null
  return (
    <div className="border-t border-slate-200 dark:border-slate-800">
      <h3 className="px-5 pt-4 text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        AI services
      </h3>
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {data.providers.map((provider) => {
          const { state, label } = providerState(provider)
          return (
            <li key={provider.capability} className="flex items-center justify-between gap-4 px-5 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">{CAPABILITIES[provider.capability]}</p>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                  {provider.model ? `${provider.provider} · ${provider.model}` : provider.provider}
                </p>
              </div>
              <span className="flex shrink-0 items-center gap-2 text-sm" data-testid={`ai-${provider.capability}`}>
                <StatusDot state={state} />
                {label}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

const CHECK_LABELS: Record<string, { label: string; description: string }> = {
  api: { label: 'API server', description: 'FastAPI backend is reachable' },
  database: { label: 'PostgreSQL', description: 'Database connection is healthy' },
  pgvector: { label: 'pgvector', description: 'Vector extension is installed' },
  redis: { label: 'Redis', description: 'Job queue, rate limits and caches' },
  workers: { label: 'Ingestion workers', description: 'Process uploaded documents' },
}

function StatusDot({ state }: { state: CheckState }) {
  const color = {
    ok: 'bg-emerald-500',
    fail: 'bg-rose-500',
    pending: 'bg-slate-300 animate-pulse',
    off: 'bg-slate-300 dark:bg-slate-600',
  }[state]
  return <span aria-hidden className={`inline-block size-2.5 rounded-full ${color}`} />
}

export function SystemStatusCard() {
  const { liveness, readiness, error, loading, refresh } = useSystemStatus()

  const stateOf = (ok: boolean | undefined): CheckState => (loading ? 'pending' : ok ? 'ok' : 'fail')
  const checks: [string, CheckState][] = [
    ['api', stateOf(liveness?.status === 'ok')],
    ['database', stateOf(readiness?.checks.database)],
    ['pgvector', stateOf(readiness?.checks.pgvector)],
    ['redis', stateOf(readiness?.services?.redis)],
    ['workers', stateOf(Boolean(readiness?.services?.ingestion_workers))],
  ]
  const workers = readiness?.services?.ingestion_workers ?? 0
  const detail = (name: string, state: CheckState) => {
    if (name !== 'workers' || state === 'pending')
      return { ok: 'Operational', fail: 'Unavailable', pending: 'Checking…', off: 'Off' }[state]
    return workers ? `${workers} running` : 'None running'
  }
  const allOk = !loading && checks.every(([, state]) => state === 'ok')

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <StatusDot state={loading ? 'pending' : allOk ? 'ok' : 'fail'} />
          <h2 className="text-sm font-semibold" role="status">
            {loading ? 'Checking services…' : allOk ? 'All systems operational' : 'Some services are unavailable'}
          </h2>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {checks.map(([name, state]) => (
          <li key={name} className="flex items-center justify-between gap-4 px-5 py-3.5">
            <div>
              <p className="text-sm font-medium">{CHECK_LABELS[name].label}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">{CHECK_LABELS[name].description}</p>
            </div>
            <span className="flex shrink-0 items-center gap-2 text-sm" data-testid={`check-${name}`}>
              <StatusDot state={state} />
              {detail(name, state)}
            </span>
          </li>
        ))}
      </ul>

      <AIServices />

      {error && (
        <div role="alert" className="border-t border-rose-200 bg-rose-50 px-5 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          {error.message}
        </div>
      )}

      {liveness && (
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          v{liveness.version} · {liveness.environment} ·{' '}
          <a className="underline hover:text-slate-700 dark:hover:text-slate-300" href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
        </p>
      )}
    </section>
  )
}
