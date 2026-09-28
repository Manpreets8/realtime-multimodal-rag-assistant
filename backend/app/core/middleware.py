"""Request-ID and latency middleware.

Implemented as pure ASGI middleware (not BaseHTTPMiddleware) so it does not
buffer streaming responses and passes WebSocket connections through untouched.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.request")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        # Accept a caller-supplied ID only if it is safe to echo into logs/headers.
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request_id_ctx.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )


# Responses are JSON or file downloads, never pages, so the strictest CSP applies. The
# interactive API docs load Swagger UI / ReDoc from a CDN and are left without a CSP.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Defensive headers on every HTTP response:
    - X-Content-Type-Options: browsers must not guess content types (a text file can't become HTML).
    - Content-Security-Policy / X-Frame-Options: API responses can't run scripts or be framed.
    - Referrer-Policy: URLs never leak to other sites.
    - Strict-Transport-Security in production (served over HTTPS behind a proxy).
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(_DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if not is_docs:
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_wrapper)
