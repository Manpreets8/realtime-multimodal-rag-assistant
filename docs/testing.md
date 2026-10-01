# Testing

How the application is tested, and how to run the suites. Back to the [README](../README.md).

## Testing

```bash
cd backend  && pytest                  # 526 tests: unit + integration + real-model tests; needs
                                       # `docker compose up -d postgres redis`. Integration tests use a separate
                                       # rag_assistant_test database and Redis database 15
cd backend  && pytest -m "not integration and not model"   # no PostgreSQL, Redis or model download (~10 s)
cd backend  && pytest --cov            # coverage report (configured in pyproject.toml)
cd backend  && ruff check . && ruff format --check .
cd backend  && pip-audit -r requirements.txt -r requirements-dev.txt
cd frontend && npm test                # 163 tests (Vitest + Testing Library)
cd frontend && npm run test:coverage
cd frontend && npm run lint && npm run typecheck && npm audit
```

**Coverage** (measured in Phase 15): backend **94%** of lines and branches, frontend **94.6%** of lines (81% of branches; Phase 17). Backend coverage runs with `concurrency = ["greenlet", "thread"]`. Without it, coverage.py misses code that SQLAlchemy's async layer runs in greenlets and under-reports modules like the services by up to 30 points.

What the suites cover:
- **Backend:** authentication, authorization, uploads, extraction, chunking, embeddings, retrieval, reranking, the RAG pipeline, citations, chat, WebSocket streaming, images, voice, the Redis queue and worker (including crash takeover), rate limits, maintenance and evaluation. Also the security matrices, the error catalogue, and startup and shutdown.
- **Frontend:** pages and flows against a mocked API, plus request timeouts, uploads over XHR and the error boundary.
- **Real models:** tests marked `model` run the actual embedding, reranking, Whisper and Piper models.

**Accessibility** (Phase 17): every page was audited with axe-core (WCAG 2.2 AA rules) in a real browser, in light and dark themes at desktop size and on key pages at phone size. The first audit found 25 violations: unlabelled file inputs, and grey or brand-coloured text below 4.5:1 contrast, mostly in dark mode. After the fixes the audit reports **0 violations**. The audit script drives the running app with Playwright, so it isn't part of `npm test`.
