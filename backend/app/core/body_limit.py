"""Reject oversized request bodies before they are parsed or spooled to disk.

FastAPI reads the whole multipart body before a route runs, so a per-route check
is too late to stop a client from streaming gigabytes at us. This middleware
rejects on Content-Length up front and counts bytes for chunked uploads.
"""

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

# Room for multipart boundaries and form fields on top of the largest allowed file.
_MULTIPART_OVERHEAD = 1024 * 1024
_STATUS = 413
_MESSAGE = "The request body is too large."


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = get_settings().max_file_size + _MULTIPART_OVERHEAD
        content_length = dict(scope["headers"]).get(b"content-length", b"")
        if content_length.isdigit() and int(content_length) > limit:
            request_id = scope.get("state", {}).get("request_id")
            error = {"code": "payload_too_large", "message": _MESSAGE, "request_id": request_id}
            await JSONResponse({"error": error}, status_code=_STATUS)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so the standard
                    # handler turns this into the usual 413 error envelope.
                    raise HTTPException(_STATUS, _MESSAGE)
            return message

        await self.app(scope, limited_receive, send)
