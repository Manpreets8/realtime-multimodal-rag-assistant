import asyncio
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.routes import chat_socket
from app.core.config import get_settings
from app.llm.base import LLMNotConfiguredError, LLMTimeoutError
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM
from tests.ws_client import WebSocketRejected, websocket

pytestmark = pytest.mark.integration

HANDBOOK = {
    "leave.txt": b"Annual leave: employees receive 18 days of paid annual leave each year. "
    b"Up to 5 unused days carry over to the next year.",
    "remote.txt": b"Remote work: staff may work remotely three days per week with manager approval.",
}


@pytest.fixture
async def alice_token(register_user: RegisterFn) -> str:
    return (await register_user(email="alice@example.com"))["access_token"]


@pytest.fixture
async def kb_id(client: AsyncClient, alice_token: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    headers = bearer(alice_token)
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "Handbook"}, headers=headers)).json()[
        "id"
    ]
    for filename, content in HANDBOOK.items():
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": (filename, content)},
            headers=headers,
        )
        await process_document(uuid.UUID(response.json()["id"]))
    return kb


def streamed_text(events: list[dict]) -> str:
    text = ""
    for event in events:
        if event["type"] == "restart":
            text = ""
        elif event["type"] == "delta":
            text += event["text"]
    return text


# --- a full turn ------------------------------------------------------------------------------


async def test_streams_progress_sources_and_text_then_saves_the_turn(
    app: FastAPI, client: AsyncClient, alice_token: str, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.answer = "Employees receive 18 days of paid annual leave per year."
    llm.cite = [(0, "employees receive 18 days of paid annual leave each year")]

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json(
            {"type": "chat", "id": "t1", "message": "How much annual leave?", "knowledge_base_id": kb_id}
        )
        events = await ws.receive_until("done")

    assert all(event["id"] == "t1" for event in events)
    kinds = [event["type"] for event in events]
    stages = [event["stage"] for event in events if event["type"] == "status"]
    assert stages == ["retrieving", "reranking", "generating"]
    assert kinds.index("sources") < kinds.index("delta") < kinds.index("done")
    assert kinds.count("delta") == len(llm.answer.split())  # word by word, as generated
    sources = next(event for event in events if event["type"] == "sources")["sources"]
    assert sources[0] == {"number": 1, "filename": "leave.txt", "page_number": None, "section": None}
    assert "content" not in sources[0]  # the full passages come with the saved answer

    done = events[-1]
    assistant = done["response"]["assistant_message"]
    assert streamed_text(events) == assistant["content"] == llm.answer
    assert assistant["answer_type"] == "knowledge_base"
    assert assistant["citations"][0]["filename"] == "leave.txt"
    assert len(done["request_id"]) == 32

    # Saved exactly as the REST endpoint would have saved it.
    conversation_id = done["response"]["conversation"]["id"]
    stored = (
        await client.get(f"/api/v1/conversations/{conversation_id}", headers=bearer(alice_token))
    ).json()
    assert [m["content"] for m in stored["messages"]] == ["How much annual leave?", llm.answer]
    assert stored["messages"][1] == assistant


async def test_follow_up_in_the_same_connection_reports_the_rewrite(
    app: FastAPI, alice_token: str, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.rewrite = "Can unused annual leave days carry over to the next year?"
    async with websocket(app, token=alice_token) as ws:
        await ws.send_json(
            {"type": "chat", "id": "a", "message": "Annual leave?", "knowledge_base_id": kb_id}
        )
        first = (await ws.receive_until("done"))[-1]["response"]
        await ws.send_json(
            {
                "type": "chat",
                "id": "b",
                "message": "And can it carry over?",
                "conversation_id": first["conversation"]["id"],
            }
        )
        events = await ws.receive_until("done")

    stages = [event["stage"] for event in events if event["type"] == "status"]
    assert stages == ["rewriting", "retrieving", "reranking", "generating"]
    assert events[-1]["response"]["conversation"]["message_count"] == 4
    assert len(llm.rewrite_calls) == 1


async def test_general_chat_streams_without_retrieval(
    app: FastAPI, alice_token: str, llm: ScriptedLLM
) -> None:
    llm.answer = "RAG combines search with generation."

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "g", "message": "What is RAG?"})
        events = await ws.receive_until("done")

    assert [e["stage"] for e in events if e["type"] == "status"] == ["generating"]
    assert not any(e["type"] == "sources" for e in events)
    assert streamed_text(events) == llm.answer


async def test_a_mid_stream_fallback_discards_the_declined_text(
    app: FastAPI, alice_token: str, llm: ScriptedLLM
) -> None:
    llm.declined_prefix = "Partial text from a model that declined"
    llm.answer = "The fallback model's answer."

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "f", "message": "Hello"})
        events = await ws.receive_until("done")

    kinds = [event["type"] for event in events]
    assert kinds.index("restart") > kinds.index("delta")
    assert streamed_text(events) == "The fallback model's answer."
    assert events[-1]["response"]["assistant_message"]["content"] == "The fallback model's answer."


# --- cancelling and failures -------------------------------------------------------------------


async def test_cancel_stops_generation_and_saves_nothing(
    app: FastAPI, client: AsyncClient, alice_token: str, llm: ScriptedLLM
) -> None:
    llm.answer = "This answer will never be finished."
    llm.gate = asyncio.Event()  # never set: generation stalls after the first word

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "c1", "message": "Tell me a long story"})
        await ws.receive_until("delta")
        await ws.send_json({"type": "cancel", "id": "c1"})
        assert (await ws.receive_json()) == {"type": "cancelled", "id": "c1"}

        # The connection stays usable.
        llm.gate = None
        await ws.send_json({"type": "chat", "id": "c2", "message": "Short one"})
        done = (await ws.receive_until("done"))[-1]

    conversations = (await client.get("/api/v1/conversations", headers=bearer(alice_token))).json()
    assert [c["title"] for c in conversations] == ["Short one"]
    assert done["response"]["conversation"]["message_count"] == 2


async def test_disconnecting_mid_answer_saves_nothing(
    app: FastAPI, client: AsyncClient, alice_token: str, llm: ScriptedLLM
) -> None:
    llm.gate = asyncio.Event()

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "d", "message": "Question"})
        await ws.receive_until("delta")
        await ws.disconnect()  # the server cancels the turn and the handler returns

    assert (await client.get("/api/v1/conversations", headers=bearer(alice_token))).json() == []


async def test_one_turn_at_a_time(app: FastAPI, alice_token: str, llm: ScriptedLLM) -> None:
    llm.gate = asyncio.Event()

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "one", "message": "First"})
        await ws.receive_until("delta")
        await ws.send_json({"type": "chat", "id": "two", "message": "Second"})
        busy = await ws.receive_json()
        llm.gate.set()
        done = (await ws.receive_until("done"))[-1]

    assert busy["type"] == "error" and busy["id"] == "two" and busy["error"]["code"] == "busy"
    assert done["id"] == "one"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMTimeoutError("The AI model took too long to respond."), "llm_timeout"),
        (LLMNotConfiguredError(), "llm_not_configured"),
    ],
    ids=["timeout", "not-configured"],
)
async def test_errors_use_the_standard_envelope_and_keep_the_connection(
    app: FastAPI, client: AsyncClient, alice_token: str, llm: ScriptedLLM, error: Exception, code: str
) -> None:
    llm.fail_with = error

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "e", "message": "Hello"})
        failed = (await ws.receive_until("error"))[-1]
        llm.fail_with = None
        await ws.send_json({"type": "chat", "id": "e2", "message": "Hello again"})
        await ws.receive_until("done")

    assert failed["id"] == "e"
    assert failed["error"]["code"] == code
    assert len(failed["error"]["request_id"]) == 32
    titles = [
        c["title"] for c in (await client.get("/api/v1/conversations", headers=bearer(alice_token))).json()
    ]
    assert titles == ["Hello again"]  # the failed turn stored nothing


async def test_unexpected_errors_do_not_leak_internals(
    app: FastAPI, alice_token: str, llm: ScriptedLLM
) -> None:
    llm.fail_with = RuntimeError("db password is hunter2")

    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "x", "message": "Hello"})
        failed = (await ws.receive_until("error"))[-1]

    assert failed["error"]["code"] == "internal_error"
    assert "hunter2" not in str(failed)


async def test_invalid_requests_get_validation_errors(app: FastAPI, alice_token: str) -> None:
    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "v", "message": "   "})
        empty = await ws.receive_json()
        await ws.send_json({"type": "chat", "message": "no id"})
        no_id = await ws.receive_json()
        await ws.send_text("not json")
        garbage = await ws.receive_json()
        await ws.send_json({"type": "dance"})
        unknown = await ws.receive_json()
        await ws.send_bytes(b"\x00\x01")
        binary = await ws.receive_json()

    assert empty["error"]["code"] == "validation_error" and empty["id"] == "v"
    assert empty["error"]["details"][0]["message"] == "Value error, Send a message, an image, or both."
    assert [e["error"]["code"] for e in (no_id, garbage, unknown, binary)] == ["bad_request"] * 4


async def test_oversized_frames_close_the_connection(app: FastAPI, alice_token: str) -> None:
    async with websocket(app, token=alice_token) as ws:
        await ws.send_text("x" * (chat_socket.MAX_FRAME_CHARS + 1))
        events, code = await ws.expect_close()

    assert code == 1009
    assert events[0]["error"]["code"] == "payload_too_large"


# --- authentication and isolation --------------------------------------------------------------


async def test_authentication_is_required_first(app: FastAPI, alice_token: str) -> None:
    async with websocket(app) as ws:
        await ws.send_json({"type": "chat", "id": "x", "message": "Hi"})
        events, code = await ws.expect_close()
    assert code == 4401 and events[0]["error"]["code"] == "unauthorized"

    async with websocket(app) as ws:
        await ws.send_json({"type": "auth", "token": alice_token[:-4] + "AAAA"})
        events, code = await ws.expect_close()
    assert code == 4401 and "invalid or has expired" in events[0]["error"]["message"]


async def test_authentication_times_out(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_socket, "AUTH_TIMEOUT_SECONDS", 0.05)

    async with websocket(app) as ws:
        events, code = await ws.expect_close()

    assert code == 4408 and events[0]["error"]["message"] == "Authentication timed out."


async def test_logging_out_ends_the_socket_session(
    app: FastAPI, client: AsyncClient, alice_token: str, llm: ScriptedLLM
) -> None:
    async with websocket(app, token=alice_token) as ws:
        await client.post("/api/v1/auth/logout", headers=bearer(alice_token))
        await ws.send_json({"type": "chat", "id": "after", "message": "Still there?"})
        events, code = await ws.expect_close()

    assert code == 4401
    assert events[0]["id"] == "after" and events[0]["error"]["message"].startswith("Your session has ended")
    assert llm.calls == []


async def test_signing_out_everywhere_ends_an_open_socket(
    app: FastAPI, client: AsyncClient, alice_token: str, llm: ScriptedLLM
) -> None:
    async with websocket(app, token=alice_token) as ws:
        await client.post("/api/v1/auth/logout-all", headers=bearer(alice_token))
        await ws.send_json({"type": "chat", "id": "after", "message": "Still there?"})
        events, code = await ws.expect_close()

    assert code == 4401 and "signed out" in events[0]["error"]["message"]
    assert llm.calls == []


async def test_other_users_conversations_and_knowledge_bases_are_not_found(
    app: FastAPI,
    client: AsyncClient,
    alice_token: str,
    kb_id: str,
    register_user: RegisterFn,
    llm: ScriptedLLM,
) -> None:
    async with websocket(app, token=alice_token) as ws:
        await ws.send_json({"type": "chat", "id": "a", "message": "Leave?", "knowledge_base_id": kb_id})
        conversation_id = (await ws.receive_until("done"))[-1]["response"]["conversation"]["id"]
    bob_token = (await register_user(email="bob@example.com"))["access_token"]
    calls_before = len(llm.calls)

    async with websocket(app, token=bob_token) as ws:
        await ws.send_json({"type": "chat", "id": "b1", "message": "Hi", "conversation_id": conversation_id})
        conversation = await ws.receive_json()
        await ws.send_json({"type": "chat", "id": "b2", "message": "Hi", "knowledge_base_id": kb_id})
        knowledge_base = (await ws.receive_until("error"))[-1]

    assert conversation["error"]["code"] == "not_found"
    assert knowledge_base["error"]["code"] == "not_found"
    assert len(llm.calls) == calls_before  # nothing was generated for bob


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
async def test_cross_site_origins_are_rejected(app: FastAPI, origin: str) -> None:
    with pytest.raises(WebSocketRejected) as rejected:
        async with websocket(app, origin=origin):
            pass
    assert rejected.value.code == 1008


async def test_clients_without_an_origin_header_may_connect(app: FastAPI, alice_token: str) -> None:
    async with websocket(app, token=alice_token, origin=None) as ws:
        await ws.send_json({"type": "chat", "id": "cli", "message": "Hi"})
        assert (await ws.receive_until("done"))[-1]["type"] == "done"
