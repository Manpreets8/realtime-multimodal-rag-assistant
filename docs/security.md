# Security and error handling

Authentication, the security measures in place, and how failures are reported and debugged. Back to the [README](../README.md).

## Authentication

| Endpoint | Description |
|---|---|
| `POST /api/v1/auth/register` | Create an account (`email`, `password`, optional `full_name`). Returns a token, so the user is signed in immediately. 409 if the email exists. |
| `POST /api/v1/auth/login` | Exchange email and password for an access token. |
| `POST /api/v1/auth/logout` | Revoke the current token (204). |
| `GET /api/v1/auth/me` | The current user. |

Design notes:
- **Passwords** are hashed with Argon2id (`pwdlib`). Hashing runs in a thread pool so it doesn't block the event loop, and hashes are upgraded automatically if the hashing parameters change.
- **Access tokens** are HS256 JWTs with `sub`, `jti`, `type`, `iat` and `exp`. The algorithm is pinned when decoding, so `alg: none` and algorithm-switching tokens are rejected.
- **Logout** records the token's `jti` in `revoked_tokens` until the token expires, so a logged-out token stops working immediately rather than living on until expiry. Expired rows are pruned on each logout.
- **Login errors** use the same message for an unknown email and a wrong password, and unknown emails still run a dummy hash, so neither the response nor its timing reveals whether an account exists.
- **Protecting a route** only needs `current_user: CurrentUser`. Every later user-owned resource (knowledge bases, documents, conversations) is filtered by `current_user.id`.
- **Frontend:** the token is kept in `localStorage` and sent as a `Bearer` header. Any 401 on an authenticated request ends the session. The trade-off: `localStorage` is readable by any script on the page. That's mitigated by never rendering untrusted HTML (answers are rendered as React elements), a strict Content-Security-Policy that allows only the app's own scripts, and short-lived tokens.
- **Welcome email:** after a successful sign-up, a welcome email (plain text plus HTML) is sent over SMTP ([email_service.py](../backend/app/services/email_service.py)). It runs as a background task after the response, so a slow or unreachable mail server never delays or fails sign-up. Temporary failures (connection errors, timeouts, 4xx replies) are retried twice; permanent 5xx replies such as bad credentials are not. Logs name the user ID, never the address. The trade-off: an email still being retried is lost if the API process restarts at that moment. Tests send through a real local SMTP server (aiosmtpd), and the test suite can never reach a real mail server.
- **Rate limiting** of logins and sign-ups is in place (see [Security](#security)). Refresh tokens are not implemented; sessions last `ACCESS_TOKEN_EXPIRE_MINUTES` (60 by default).

## Security

| Area | What is in place |
|---|---|
| Passwords | Argon2 (pwdlib). Strength rules on sign-up. Login takes the same time whether or not the account exists |
| Sessions | JWT with the algorithm pinned (`alg: none` and tampered tokens are rejected, and tested). Logout revokes tokens server-side. Production refuses to start with a weak `JWT_SECRET` |
| Authorization | Every query is scoped to the signed-in user. Another user's resource returns **404**, identical to a missing one, so its existence isn't revealed |
| Input | Pydantic validation on every request. Uploads are checked by content, not extension. Limits on file size, PDF pages, DOCX zip-bomb ratio and image pixels |
| SQL | Parameterised queries throughout. The only interpolated statement (`SET LOCAL hnsw.ef_search`, which can't take parameters) uses an integer built in code |
| Abuse | Rate limits on logins, sign-ups, LLM calls, uploads and voice (Redis, shared by all API processes) |
| Browser | CORS allows the configured origins only, with no credentials (bearer tokens, no cookies) and only the methods and headers the app uses. The WebSocket checks `Origin` and receives its token in the first frame, never in the URL |
| Headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a strict CSP on API responses (not on `/docs`, which needs its CDN scripts), and HSTS in production. Uploaded files are served as attachments; only PDFs may open inline |
| Secrets | Settings hold keys as `SecretStr`, so they never appear in reprs. Log fields named like credentials are redacted. Error responses never contain stack traces, SQL or provider replies |
| Dependencies | `pip-audit` and `npm audit`: no known vulnerabilities (at Phase 15) |

**Tested as a whole, not just per feature** ([test_security_hardening.py](../backend/tests/test_security_hardening.py)):
- **Authentication matrix.** Every operation in the OpenAPI schema, except the four public ones, must reject both anonymous and forged tokens with 401.
- **Authorization matrix.** Every operation with a resource ID must return 404 to another user. So must 7 operations that take a foreign ID in the body (a knowledge base, conversation or image), and none of them may reach the LLM. A new endpoint fails the test until it is added to the matrix.
- **Injection.** SQL, tsquery operators, path traversal, XSS strings and Unicode are stored and searched as plain data.
- **Secret leakage.** A known JWT secret, password, token and API-key canary must not appear in any response or log line across failing logins, validation errors and LLM errors. The test also asserts that log records were captured, so it can't pass vacuously.

## Error handling

Every error uses one envelope, `{"error": {"code", "message", "request_id", "details"}}`, with a message a user can act on. Technical details go only to the structured log, under the same `request_id`. [test_error_catalogue.py](../backend/tests/test_error_catalogue.py) walks the spec's list through the API:

| Failure | Response |
|---|---|
| Invalid login / unauthorized | 401 `unauthorized` |
| Missing knowledge base (or someone else's) | 404 `not_found` |
| Invalid / unsupported / oversized document | 422 `invalid_document` / 415 `unsupported_file_type` / 413 `file_too_large` |
| Failed document processing | Document status `failed` with the reason shown in the UI ("password-protected", "no readable text…") |
| Embedding failure | During ingestion, the document's failure reason; during a search, answer or chat, 502 `embedding_error` |
| Vector database / database down | 503 `database_unavailable` or `service_unavailable` with `Retry-After` (verified by stopping PostgreSQL); the app recovers by itself when it is back |
| Slow query | 504 `timeout`: every connection has a 30 s `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`) |
| LLM failure / timeout / refusal | 502 `llm_error`, 503 `llm_unavailable` / `llm_not_configured`, 504 `llm_timeout`, 422 `llm_refusal`. Nothing is saved |
| Speech-to-text / text-to-speech failure | 502 `stt_error` / `tts_error`, 422 `no_speech` / `nothing_to_speak` |
| Image processing failure | 422 `invalid_image` |
| WebSocket disconnect | Generation stops and nothing is saved. The UI says the connection was lost and offers Retry |
| Rate limit | 429 `rate_limited` with `Retry-After` |
| Browser-side timeout | Requests give up after 30 s, or 180 s for LLM and speech calls (longer than the server's own LLM timeout, so the server's specific error arrives first): "The server took too long to respond" |
| Rendering bug | An error boundary shows "Something went wrong" with Reload and Dashboard buttons instead of a blank page, and recovers on navigation |

### Debugging a failed request

1. The user sees a reference: "Ref b5f5d149" next to a failed chat message, or the request ID in error panels for server errors.
2. Search the JSON logs for it: `grep b5f5d149 api.log`. Every log line of that request carries the same `request_id`: the request line (path, status, duration) and the failure with its error code. For RAG requests there are also the retrieval counts and per-stage timings (`retrieval_ms`, `rerank_ms`, `llm_ms`), token usage and Anthropic's own request ID (`anthropic_request_id`, for support tickets with Anthropic). WebSocket turns and worker jobs log under their own IDs (`job-<id>` for ingestion).
3. A turn that fails is not saved, so retrying is safe.

## Error format

Every error response uses the same shape:

```json
{ "error": { "code": "not_found", "message": "…", "request_id": "…", "details": null } }
```

`request_id` matches the `X-Request-ID` response header and the `request_id` field in backend logs, so a user-reported error can be traced to its log lines.
