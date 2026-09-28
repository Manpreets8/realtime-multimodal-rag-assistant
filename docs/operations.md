# Background jobs, Redis and deployment

The ingestion worker, what Redis is used for, and running the application in production. Back to the [README](../README.md).

## Redis and background jobs

Redis is used where it gives a concrete benefit, never as the source of truth. Documents, messages and users stay in PostgreSQL, so losing Redis data loses at most cached values and queued jobs, and the maintenance sweep re-queues those from the database.

| Workload | Why Redis |
|---|---|
| **Ingestion jobs** (a Stream with a consumer group) | Parsing and embedding are CPU- and memory-heavy, so they run in **separate worker processes**, and the API stays responsive. Jobs survive restarts; any number of workers share them. |
| **Rate limiting** | Counters shared by every API process. An in-memory limiter would multiply the limit by the number of processes. |
| **Temporary state** | Live progress ("Embedding 128/400 chunks"), per-document processing locks and worker heartbeats. These change constantly and are worthless afterwards, so a TTL fits better than a database row. |
| **Caches** | TTS audio and query embeddings, shared by all API processes and kept across restarts. |
| **Scheduling lock** | Periodic maintenance runs on exactly one worker per interval. |

**Ingestion worker** ([ingestion_worker.py](../backend/app/workers/ingestion_worker.py)):
- **At-least-once delivery.** A job is acknowledged only after the result is stored in PostgreSQL. Processing is idempotent (a completed document is skipped), so a repeated job is harmless.
- **Crash takeover.** While a job runs, the worker refreshes it every few seconds. A job silent for `INGESTION_JOB_TIMEOUT_SECONDS` (60 s) belongs to a dead worker, and the next free worker claims it (`XAUTOCLAIM`). Long documents are never taken over while their worker is alive.
- **One worker per document.** A lock (`SET NX`, refreshed with the heartbeat, released only by its owner through a Lua compare-and-delete) stops a duplicate job from processing a document twice at once.
- **Retries and dead letters.** If the database or storage is down, the processor raises and the job stays pending, to be retried after the timeout. After `INGESTION_MAX_DELIVERIES` (3) attempts, the job goes to a dead-letter stream and the document is marked failed with an explanation, so a poison document can't crash workers forever. Document-level problems (a corrupt PDF, no text) are not retried: they are recorded as the document's failure reason, as before.
- **Graceful shutdown.** Ctrl+C or SIGTERM stops new work and finishes the current job.
- **Maintenance**, every `MAINTENANCE_INTERVAL_SECONDS` (300 s), on one worker:
  - re-queue documents that were never queued (Redis was down at upload time) or whose job was lost;
  - delete unsent images older than 24 h, with their files;
  - purge expired logout entries.

**Rate limits** (per user unless noted; all configurable; `429` with `Retry-After`, the standard error envelope, and a message such as "Please try again in 42 seconds"):

| Scope | Default | Applies to |
|---|---|---|
| `RATE_LIMIT_CHAT` | 20/1m | Chat (REST and WebSocket turns), RAG answers, image questions: the LLM spend |
| `RATE_LIMIT_UPLOADS` | 60/1h | Document and image uploads |
| `RATE_LIMIT_VOICE` | 30/1m | Transcription and speech synthesis |
| `RATE_LIMIT_LOGIN` | 10/15m | Per client address **and** account, so it slows password guessing without letting one client lock a user out everywhere |
| `RATE_LIMIT_REGISTER` | 10/1h | Per client address |

The algorithm is a sliding-window counter: two fixed windows weighted by how far the current one has progressed. That's O(1) memory per client, avoids a fixed window's double burst at the boundary, and the check and increment are atomic in one Lua script. If Redis is unreachable, requests are **allowed** and a warning is logged, because the limiter protects the service and shouldn't take it down. Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>` so limits apply to real client addresses.

**Memory policy.** `docker-compose.yml` runs Redis with AOF persistence (queued jobs survive a Redis restart) and `maxmemory-policy volatile-lru`, so under memory pressure only keys with a TTL (caches, rate-limit windows) are evicted, never the job stream or locks.

**Tests** run against a real Redis on database 15, flushed before every test, with a guard that refuses any other database. The worker tests cover:
- two workers sharing a queue;
- takeover after a simulated crash;
- heartbeats keeping a long job with its worker;
- retry after a transient failure, and dead-lettering after repeated failures;
- duplicate jobs under the lock;
- graceful stop.

**A bug worth recording.** One worker test failed about 1 run in 2. A stack dump of the stuck tasks showed the heartbeat task marked *cancelling* but back asleep: its cancellation had been swallowed. On Python 3.11, a cancel that lands inside redis-py's internal `asyncio.timeout` block can surface as a `TimeoutError`, a known 3.11 race fixed in 3.12. redis-py re-raises that as its own `TimeoutError`, the loop's `except RedisError` caught it, and the loop carried on. The worker's loops now end on an explicit event rather than depending on `cancel()`; cancel is only a backstop. After the fix: 15 of 15 runs of the standalone reproduction and 6 of 6 runs of the test file passed.

**Verified end to end** with the real API, two worker processes, Redis, Chromium and a 180-page PDF (1,080 chunks):
- **Live progress.** The page showed "Embedding 64/1080 chunks" with a progress bar.
- **Crash takeover.** The worker processing the PDF was killed with `taskkill /F` mid-embedding. The other worker claimed the job about 10 s later (the job timeout used for the test) and indexed all 1,080 chunks with unique indexes, 32.8 s after the kill; re-processing took 19.7 s, about 55 chunks/s.
- **No worker.** With every worker killed, a new upload showed the "waiting for an ingestion worker" notice. Starting a worker indexed it about 4 s later and the notice disappeared.
- **Redis outage.** With `docker stop` on Redis, an upload still returned 201 (queued in the database); readiness stayed `ready` with `redis: false`; chat still answered normally, because the rate limiter failed open (it returned the expected "LLM not configured" error, not a 500). After Redis restarted, the maintenance sweep re-queued the upload and it was indexed 41 s later: a 30 s grace period plus the 15 s test interval.
- **Rate limiting.** The 11th login attempt for one account from one address got 429 with `Retry-After: 110`.
- **Maintenance.** It ran on one worker per interval, alternating between them, and purged two genuinely expired logout entries from the development database.

**Measured: query-embedding cache.** With the local model, embedding a query took 6.4 ms on average uncached and 1.1 ms from the cache, saving about 5 ms per search. That's small here. The cache matters for API embedding providers (`EMBEDDING_PROVIDER=voyage`), where each uncached query is a network round trip and a billed request, and for repeated questions and evaluation runs.

## Deployment

`docker-compose.prod.yml` turns the same stack into a single-server production deployment. It's meant for a VM with Docker (for example 2 vCPU and 4 GB RAM), a domain pointing at it, and ports 80 and 443 open.

```bash
# .env on the server
DOMAIN=rag.example.com
JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
POSTGRES_PASSWORD=<another long random value>
LLM_API_KEY=<your Anthropic API key>

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

What changes compared with the local stack:

| | Local (`docker-compose.yml`) | Production (with `docker-compose.prod.yml`) |
|---|---|---|
| Entry point | nginx on :8080 (HTTP) | **Caddy** on :80/:443, with a Let's Encrypt certificate obtained and renewed automatically, HTTP redirected to HTTPS, HTTP/3 |
| Published ports | 8080, plus PostgreSQL 5433 and Redis 6380 for development | Only 80 and 443; PostgreSQL, Redis, the API and nginx are internal |
| Mode | `development` | `production`: refuses to start with a weak `JWT_SECRET` or wildcard CORS, and sends HSTS |
| Secrets | Development defaults | `DOMAIN`, `JWT_SECRET` and `POSTGRES_PASSWORD` must be set, or Compose stops with a clear message |
| Client addresses | nginx ignores `X-Forwarded-For` | nginx accepts `X-Forwarded-For` only from Caddy's network (a fixed subnet), so rate limits apply to real clients |

**Operating it:**
- **Updates:** `git pull`, then the same `up -d --build`. Migrations run automatically before the API starts. Workers finish their current document on shutdown (`stop_grace_period` is 90 s).
- **Backups:** `docker compose exec postgres pg_dump -U rag rag_assistant | gzip > backup.sql.gz`, plus the `uploads` volume. Everything in Redis can be rebuilt; queued jobs are re-queued from the database by the maintenance sweep.
- **Scaling:** add workers with `--scale worker=N`. For more API capacity, run more API containers behind nginx; rate limits and caches are already shared through Redis. Managed PostgreSQL (with pgvector) and Redis can replace the containers by pointing `DATABASE_URL` and `REDIS_URL` at them.
- **Other platforms:** the images are ordinary containers. On a PaaS, run the backend image three ways (the API, `python -m app.workers.ingestion_worker`, and `alembic upgrade head` as a release step) and the web image in front. Only this Compose setup has been tested.

**Verified locally** with the production override, `DOMAIN=localhost` (Caddy's local certificate authority) and alternative ports:
- HTTPS with HSTS, and a 308 redirect from HTTP;
- PostgreSQL unreachable from the host;
- the full browser flow over HTTPS and a secure WebSocket (upload, worker ingestion, search, Whisper, Piper, chat), with no CSP violations.

Deploying to a public domain with a real Let's Encrypt certificate has not been done yet.

**Found while preparing this:**
- **Forgeable client address.** The first nginx configuration appended to client-supplied `X-Forwarded-For` headers, so a client could forge its address and dodge the per-address login rate limit. That was reproduced, fixed with nginx's `realip` module and a configurable trusted range, and re-tested both locally and behind Caddy.
- **Startup race.** On a fresh volume the PostgreSQL healthcheck could pass during the image's temporary initialisation server, making the migration fail intermittently. Checking over TCP fixed it: 5 of 5 fresh starts were then ready.

**CI** ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs on every push and pull request:
- backend lint, tests against real PostgreSQL and Redis, and a dependency audit;
- frontend lint, typecheck, tests, build and audit;
- a Docker build and smoke test (`docker compose up --wait`, then readiness and the app shell).

The workflow's commands were run locally, including the Docker job's `up --wait`. The workflow file itself hasn't run on GitHub, because this repository has no remote yet.
