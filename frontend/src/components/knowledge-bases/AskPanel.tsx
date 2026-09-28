import { useState, type FormEvent } from 'react'

import { ApiError } from '../../services/api'
import { askQuestion, type AnswerResponse } from '../../services/rag'
import { AnswerBody } from '../citations/AnswerBody'
import { ErrorAlert } from '../ui/Alert'
import { Button } from '../ui/Button'

export function AskPanel({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [question, setQuestion] = useState('')
  const [response, setResponse] = useState<AnswerResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [asking, setAsking] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    setError(null)
    try {
      setResponse(await askQuestion(question.trim(), [knowledgeBaseId]))
    } catch (err) {
      setResponse(null)
      setError(err instanceof ApiError ? err : new ApiError(0, 'unknown_error', 'Something went wrong.', null))
    } finally {
      setAsking(false)
    }
  }

  return (
    <div className="space-y-5">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <label htmlFor="kb-ask" className="sr-only">
          Ask a question about this knowledge base
        </label>
        <input
          id="kb-ask"
          value={question}
          maxLength={2000}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question, e.g. How many days of annual leave do I get?"
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900"
        />
        <Button type="submit" loading={asking} disabled={!question.trim()}>
          Ask
        </Button>
      </form>

      {asking && (
        <p className="text-sm text-slate-500 dark:text-slate-400" role="status">
          Searching the knowledge base and generating an answer…
        </p>
      )}
      {error && (
        <ErrorAlert requestId={error.status >= 500 && error.code !== 'llm_not_configured' ? error.requestId : null}>
          {error.message}
        </ErrorAlert>
      )}
      {response && !asking && (
        <section
          aria-label="Answer"
          className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900"
        >
          <AnswerBody
            text={response.answer}
            answerType={response.answer_type}
            citations={response.citations}
            sources={response.sources}
            truncated={response.truncated}
            model={response.model}
            usage={response.usage}
            reranked={response.retrieval?.reranked}
            timingsMs={response.timings_ms}
          />
        </section>
      )}
    </div>
  )
}
