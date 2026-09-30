"""WebSocket chat: the same turn as `POST /chat`, streamed.

Connect to `/api/v1/ws/chat`. All frames are JSON text.

Client → server
    {"type": "auth", "token": "<JWT>"}            first frame, within 10 s
    {"type": "chat", "id": "<client id>", "message": ..., "conversation_id"?, "knowledge_base_id"?,
     "image_ids"?}                                 same fields as POST /chat
    {"type": "cancel", "id": "<client id>"}      stop generating; nothing is saved

Server → client (every event about a turn carries the client's `id`)
    {"type": "ready"}                              authenticated
    {"type": "status", "stage": "rewriting" | "retrieving" | "reranking" | "generating"}
    {"type": "sources", "sources": [{number, filename, page_number, section}]}
    {"type": "delta", "text": "..."}               answer text as Claude writes it
    {"type": "restart"}                            discard streamed text (a fallback model took over)
    {"type": "done", "response": <ChatResponse>}   the saved turn: authoritative text and citations
    {"type": "cancelled"}
    {"type": "error", "error": {code, message, request_id, details?}}

The token is sent in the first frame, not the URL, so it never lands in access logs or
browser history. It is re-checked (expiry, logout) before every turn. One turn runs at a
time per connection. A turn is saved only when it completes: cancelling or disconnecting
stops generation (closing the Claude stream) and stores nothing, as with a failed REST call.
Browsers don't apply CORS to WebSockets, so the Origin header is checked against
CORS_ORIGINS here.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from enum import IntEnum
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.deps import Storage
from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError, UnauthorizedError
from app.core.logging import request_id_ctx
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import SessionLocal
from app.rag.pipeline import Stage
from app.rag.reranking import RankedChunk
from app.schemas.chat import ChatRequest
from app.services import auth_service, chat_service
from app.services.storage import LocalFileStorage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

AUTH_TIMEOUT_SECONDS = 10.0
MAX_FRAME_CHARS = 64 * 1024  # a chat request is at most ~3 KB
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CloseCode(IntEnum):
    POLICY_VIOLATION = 1008  # disallowed Origin
    MESSAGE_TOO_BIG = 1009
    UNAUTHORIZED = 4401
    AUTH_TIMEOUT = 4408


class _Closed(Exception):
    """The connection was closed by us; stop serving."""


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    allowed = get_settings().cors_origins
    # Non-browser clients send no Origin; browsers always do.
    return origin is None or "*" in allowed or origin in allowed


def _error(
    client_id: str | None, code: str, message: str, request_id: str | None, details: Any = None
) -> dict:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = details
    return {"type": "error", "id": client_id, "error": body}


class _TurnEvents:
    """Forwards one turn's progress to the socket (implements pipeline.AnswerEvents)."""

    def __init__(self, connection: "ChatConnection", client_id: str) -> None:
        self._connection = connection
        self._id = client_id

    async def stage(self, stage: Stage) -> None:
        await self._connection.send({"type": "status", "id": self._id, "stage": stage.value})

    async def sources(self, sources: list[RankedChunk]) -> None:
        listed = [
            {
                "number": number,
                "filename": ranked.chunk.filename,
                "page_number": ranked.chunk.page_number,
                "section": ranked.chunk.section,
            }
            for number, ranked in enumerate(sources, start=1)
        ]
        await self._connection.send({"type": "sources", "id": self._id, "sources": listed})

    async def text(self, delta: str) -> None:
        if delta:
            await self._connection.send({"type": "delta", "id": self._id, "text": delta})

    async def restart(self) -> None:
        await self._connection.send({"type": "restart", "id": self._id})


class ChatConnection:
    def __init__(self, websocket: WebSocket, storage: LocalFileStorage) -> None:
        self._ws = websocket
        self._storage = storage
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._token = ""
        self.user_id: uuid.UUID | None = None
        self.turns = 0
        self._task: asyncio.Task[None] | None = None
        self._task_id: str | None = None

    # --- transport ------------------------------------------------------------------

    async def send(self, event: dict) -> None:
        """Send an event; a connection that is already gone is ignored (the turn is cancelled
        by the receive loop)."""
        if self._closed:
            return
        async with self._send_lock:
            try:
                await self._ws.send_text(json.dumps(event, separators=(",", ":")))
            except (WebSocketDisconnect, RuntimeError, OSError):
                self._closed = True

    async def close(self, code: int, error: dict | None = None) -> None:
        if error is not None:
            await self.send(error)
        if not self._closed:
            self._closed = True
            with contextlib.suppress(RuntimeError, OSError):
                await self._ws.close(code=code)
        raise _Closed

    async def _receive(self) -> dict | None:
        """The next JSON frame; None when the client disconnected."""
        message = await self._ws.receive()
        if message["type"] == "websocket.disconnect":
            self._closed = True
            return None
        text = message.get("text")
        if text is None:
            await self.send(_error(None, "bad_request", "Send JSON text frames.", None))
            return {}
        if len(text) > MAX_FRAME_CHARS:
            await self.close(
                CloseCode.MESSAGE_TOO_BIG,
                _error(None, "payload_too_large", "The message is too large.", None),
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            await self.send(_error(None, "bad_request", "Frames must be JSON objects.", None))
            return {}
        return data

    # --- authentication ------------------------------------------------------------------

    async def _verify(self, token: str) -> uuid.UUID:
        """Same checks as the REST dependency: signature, expiry, logout, active account."""
        try:
            payload = decode_access_token(token)
        except InvalidTokenError as exc:
            raise UnauthorizedError("Your session is invalid or has expired. Please log in again.") from exc
        async with SessionLocal() as db:
            if await auth_service.is_token_revoked(db, payload.jti):
                raise UnauthorizedError("Your session has ended. Please log in again.")
            user = await auth_service.get_active_user(db, payload.subject)
        if user is None:
            raise UnauthorizedError("Your session is invalid or has expired. Please log in again.")
        if payload.session_version != user.session_version:
            raise UnauthorizedError("You were signed out because your password or sessions changed.")
        return user.id

    async def _reject(self, exc: UnauthorizedError) -> None:
        await self.close(CloseCode.UNAUTHORIZED, _error(None, exc.code, exc.message, None))

    async def authenticate(self) -> None:
        try:
            data = await asyncio.wait_for(self._receive(), AUTH_TIMEOUT_SECONDS)
        except TimeoutError:
            await self.close(
                CloseCode.AUTH_TIMEOUT, _error(None, "unauthorized", "Authentication timed out.", None)
            )
        if data is None:
            raise _Closed
        token = data.get("token")
        if data.get("type") != "auth" or not isinstance(token, str):
            await self._reject(UnauthorizedError('Authenticate first: {"type": "auth", "token": "..."}.'))
        try:
            self.user_id = await self._verify(token)
        except UnauthorizedError as exc:
            await self._reject(exc)
        self._token = token
        await self.send({"type": "ready"})

    # --- turns ------------------------------------------------------------------------------

    async def serve(self) -> None:
        try:
            while True:
                data = await self._receive()
                if data is None:
                    return
                kind = data.get("type")
                if kind == "chat":
                    await self._start_turn(data)
                elif kind == "cancel":
                    await self._cancel(data.get("id"))
                elif data:  # {} = a bad frame, already answered
                    await self.send(_error(None, "bad_request", "Unknown message type.", None))
        finally:
            if self._task is not None and not self._task.done():
                self._task.cancel()  # client gone or connection closed: stop generating, save nothing
                await asyncio.wait({self._task})

    async def _start_turn(self, data: dict) -> None:
        client_id = data.get("id")
        if not isinstance(client_id, str) or not _CLIENT_ID.match(client_id):
            await self.send(
                _error(
                    None, "bad_request", "Each chat message needs an id (1-64 letters, digits, - or _).", None
                )
            )
            return
        if self._task is not None and not self._task.done():
            await self.send(
                _error(client_id, "busy", "Wait for the current answer to finish, or cancel it.", None)
            )
            return
        try:
            user_id = await self._verify(self._token)  # expired or logged out since connecting
        except UnauthorizedError as exc:
            await self.close(CloseCode.UNAUTHORIZED, _error(client_id, exc.code, exc.message, None))
        try:
            await rate_limit.enforce(rate_limit.Scope.CHAT, str(user_id))
        except rate_limit.RateLimitedError as exc:
            await self.send(_error(client_id, exc.code, exc.message, None, exc.details))
            return
        try:
            request = ChatRequest.model_validate({k: v for k, v in data.items() if k not in ("type", "id")})
        except ValidationError as exc:
            details = [
                {"loc": ["body", *err.get("loc", ())], "message": err.get("msg"), "type": err.get("type")}
                for err in exc.errors(include_url=False, include_context=False)
            ]
            await self.send(
                _error(client_id, "validation_error", "The request contains invalid data.", None, details)
            )
            return
        self.turns += 1
        self._task_id = client_id
        self._task = asyncio.create_task(self._answer(client_id, request, user_id))

    async def _answer(self, client_id: str, request: ChatRequest, user_id: uuid.UUID) -> None:
        request_id = uuid.uuid4().hex
        request_id_ctx.set(request_id)  # this task's logs (the context was copied at creation)
        started = time.perf_counter()
        try:
            async with SessionLocal() as db:
                response = await chat_service.send_message(
                    db, self._storage, user_id, request, _TurnEvents(self, client_id)
                )
        except AppError as exc:
            logger.info("ws_chat_failed", extra={"code": exc.code, "status_code": exc.status_code})
            await self.send(_error(client_id, exc.code, exc.message, request_id, exc.details))
            return
        except Exception:
            logger.exception("ws_chat_unhandled")
            await self.send(
                _error(
                    client_id,
                    "internal_error",
                    "An unexpected error occurred. Please try again later.",
                    request_id,
                )
            )
            return
        await self.send(
            {
                "type": "done",
                "id": client_id,
                "request_id": request_id,
                "response": response.model_dump(mode="json"),
            }
        )
        logger.info(
            "ws_chat_completed", extra={"duration_ms": round((time.perf_counter() - started) * 1000, 2)}
        )

    async def _cancel(self, client_id: Any) -> None:
        task = self._task
        if task is None or task.done() or client_id != self._task_id:
            return  # already finished: the client gets (or got) `done` or `error`
        task.cancel()
        await asyncio.wait({task})
        if task.cancelled():
            logger.info("ws_chat_cancelled")
            await self.send({"type": "cancelled", "id": client_id})


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket, storage: Storage) -> None:
    connection_id = uuid.uuid4().hex
    request_id_ctx.set(connection_id)
    if not _origin_allowed(websocket):
        logger.warning("ws_origin_rejected", extra={"origin": websocket.headers.get("origin")})
        await websocket.close(code=CloseCode.POLICY_VIOLATION)  # before accept: the handshake gets 403
        return
    await websocket.accept()
    connection = ChatConnection(websocket, storage)
    started = time.perf_counter()
    try:
        await connection.authenticate()
        logger.info("ws_connected", extra={"user_id": str(connection.user_id)})
        await connection.serve()
    except (_Closed, WebSocketDisconnect):
        pass
    finally:
        if connection.user_id is not None:
            logger.info(
                "ws_disconnected",
                extra={
                    "user_id": str(connection.user_id),
                    "turns": connection.turns,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
