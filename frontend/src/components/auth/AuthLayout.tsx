import type { ReactNode } from 'react'

import { Logo } from '../layout/Logo'

const HIGHLIGHTS = [
  { title: 'Grounded answers', text: 'Responses cite the exact document and page they came from.' },
  { title: 'Multimodal', text: 'Ask with text or voice, and bring screenshots and diagrams.' },
  { title: 'Your knowledge, private', text: 'Every knowledge base is scoped to your account.' },
]

export function AuthLayout({ title, subtitle, children }: { title: string; subtitle: ReactNode; children: ReactNode }) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <aside className="relative hidden overflow-hidden bg-gradient-to-br from-brand-700 via-brand-600 to-indigo-500 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <Logo inverted />
        <div className="max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">
            Ask your documents anything. Get answers you can verify.
          </h2>
          <ul className="mt-10 space-y-6">
            {HIGHLIGHTS.map((item) => (
              <li key={item.title} className="flex gap-3">
                <span className="mt-1.5 size-2 shrink-0 rounded-full bg-white/80" aria-hidden />
                <div>
                  <p className="font-medium">{item.title}</p>
                  <p className="text-sm text-white/75">{item.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-white/60">Retrieval-augmented generation · pgvector · FastAPI · React</p>
      </aside>

      <main className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Logo />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">{subtitle}</p>
          <div className="mt-8">{children}</div>
        </div>
      </main>
    </div>
  )
}
