"""A minimal ASGI WebSocket client that runs the app on the test's own event loop.

Starlette's TestClient runs the app in a separate thread and event loop, which the
shared asyncpg engine does not allow; this speaks the ASGI WebSocket protocol directly.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI


class WebSocketRejected(Exception):
    """The server closed the connection before accepting it (HTTP 403 in a real server)."""

    def __init__(self, code: int) -> None:
        super().__init__(f"rejected with close code {code}")
        self.code = code


class Closed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"closed with code {code}")
        self.code = code


class WSClient:
    def __init__(self, app: FastAPI, path: str, headers: dict[str, str]) -> None:
        self._to_app: asyncio.Queue[dict] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict] = asyncio.Queue()
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        }
        self.task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))
        self.close_code: int | None = None

    async def connect(self) -> None:
        await self._to_app.put({"type": "websocket.connect"})
        message = await asyncio.wait_for(self._from_app.get(), 5)
        if message["type"] == "websocket.close":
            raise WebSocketRejected(message.get("code", 1000))
        assert message["type"] == "websocket.accept", message

    async def send_json(self, data: Any) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def receive_json(self, wait: float = 5) -> dict:
        message = await asyncio.wait_for(self._from_app.get(), wait)
        if message["type"] == "websocket.close":
            self.close_code = message.get("code", 1000)
            raise Closed(self.close_code)
        return json.loads(message["text"])

    async def receive_until(self, kind: str, wait: float = 5) -> list[dict]:
        """Events up to and including the first of type `kind`."""
        events = []
        while True:
            event = await self.receive_json(wait)
            events.append(event)
            if event["type"] == kind:
                return events

    async def expect_close(self, wait: float = 5) -> tuple[list[dict], int]:
        """Events sent before the server closed, and the close code."""
        events = []
        try:
            while True:
                events.append(await self.receive_json(wait))
        except Closed as closed:
            return events, closed.code

    async def disconnect(self) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, 5)


@asynccontextmanager
async def websocket(
    app: FastAPI, *, token: str | None = None, origin: str | None = "http://localhost:5173"
) -> AsyncIterator[WSClient]:
    """Connect (and authenticate when `token` is given, asserting `ready`)."""
    client = WSClient(app, "/api/v1/ws/chat", {"origin": origin} if origin else {})
    try:
        await client.connect()
        if token is not None:
            await client.send_json({"type": "auth", "token": token})
            assert (await client.receive_json()) == {"type": "ready"}
        yield client
    finally:
        if not client.task.done():
            await client.disconnect()
