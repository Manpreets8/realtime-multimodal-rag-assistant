import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import factory as llm_factory
from app.llm.base import LLMNotConfiguredError, LLMTimeoutError, TextPart
from app.llm.base import Message as LLMMessage
from app.models import Conversation, Message
from app.rag.prompts import GENERAL_SYSTEM_PROMPT, GROUNDED_SYSTEM_PROMPT
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.integration

CHAT = "/api/v1/chat"
CONVERSATIONS = "/api/v1/conversations"
HANDBOOK = {
    "leave.txt": b"Annual leave: employees receive 18 days of paid annual leave each year. "
    b"Up to 5 unused days carry over to the next year.",
    "remote.txt": b"Remote work: staff may work remotely three days per week with manager approval.",
}


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def kb_id(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "Handbook"}, headers=alice)).json()["id"]
    for filename, content in HANDBOOK.items():
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": (filename, content)},
            headers=alice,
        )
        await process_document(uuid.UUID(response.json()["id"]))
    return kb


async def chat(client: AsyncClient, headers: dict, message: str, **extra) -> dict:
    response = await client.post(CHAT, json={"message": message, **extra}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_first_message_creates_a_titled_conversation_with_a_cited_answer(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.answer = "Employees receive 18 days of paid annual leave per year."
    llm.cite = [(0, "employees receive 18 days of paid annual leave each year")]

    body = await chat(client, alice, "How many days of annual leave do I get?", knowledge_base_id=kb_id)

    conversation, user, assistant = body["conversation"], body["user_message"], body["assistant_message"]
    assert conversation["title"] == "How many days of annual leave do I get?"
    assert conversation["knowledge_base_id"] == kb_id
    assert conversation["knowledge_base_name"] == "Handbook"
    assert conversation["message_count"] == 2
    assert user["role"] == "user" and user["content"] == "How many days of annual leave do I get?"
    assert assistant["role"] == "assistant"
    assert assistant["answer_type"] == "knowledge_base" and assistant["grounded"] is True
    assert assistant["citations"][0]["filename"] == "leave.txt"
    assert assistant["sources"][0]["content"].startswith("Annual leave")
    assert assistant["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert user["created_at"] < assistant["created_at"]
    assert llm.rewrite_calls == []  # no history yet, nothing to rewrite


async def test_history_is_persisted_with_citations_and_reloads_identically(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.cite = [(0, "employees receive 18 days")]
    first = await chat(client, alice, "How much annual leave?", knowledge_base_id=kb_id)
    conversation_id = first["conversation"]["id"]

    detail = (await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=alice)).json()

    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1] == first["assistant_message"]


async def test_follow_up_is_rewritten_for_search_and_answered_with_history(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.answer = "Employees receive 18 days."
    first = await chat(client, alice, "How much annual leave do employees receive?", knowledge_base_id=kb_id)
    llm.answer = "Up to 5 unused days carry over."
    llm.rewrite = "annual leave unused days carry over"

    second = await chat(client, alice, "Can it be carried over?", conversation_id=first["conversation"]["id"])

    # Search used the rewritten standalone query...
    [rewrite] = llm.rewrite_calls
    assert "Latest message: Can it be carried over?" in rewrite["messages"][0].text
    assert second["assistant_message"]["retrieval_query"] == "annual leave unused days carry over"
    assert second["assistant_message"]["retrieval"]["rewritten"] is True
    assert "rewrite" in second["assistant_message"]["timings_ms"]
    # ...and the answer call saw the earlier turn before the new grounded question.
    answer_call = llm.answer_calls[-1]
    assert answer_call["system"] == GROUNDED_SYSTEM_PROMPT
    history, current = answer_call["messages"][:-1], answer_call["messages"][-1]
    assert history == [
        LLMMessage.user("How much annual leave do employees receive?"),
        LLMMessage.assistant("Employees receive 18 days."),
    ]
    assert current.parts[-1] == TextPart("Can it be carried over?")
    assert second["conversation"]["message_count"] == 4


async def test_general_chat_without_a_knowledge_base(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    llm.answer = "Hello! How can I help?"
    first = await chat(client, alice, "Hi there")
    await chat(client, alice, "What did I just say?", conversation_id=first["conversation"]["id"])

    assert first["assistant_message"]["answer_type"] == "general"
    assert first["conversation"]["knowledge_base_id"] is None
    last = llm.answer_calls[-1]
    assert last["system"] == GENERAL_SYSTEM_PROMPT
    assert last["messages"][0] == LLMMessage.user("Hi there")
    assert llm.rewrite_calls == []  # no retrieval in general chat, so no rewrite


async def test_switching_knowledge_base_mid_conversation(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    first = await chat(client, alice, "Hello")
    conversation_id = first["conversation"]["id"]

    switched = await chat(
        client, alice, "How much annual leave?", conversation_id=conversation_id, knowledge_base_id=kb_id
    )
    kept = await chat(client, alice, "And remote work?", conversation_id=conversation_id)
    back = await chat(client, alice, "Thanks", conversation_id=conversation_id, knowledge_base_id=None)

    assert switched["conversation"]["knowledge_base_id"] == kb_id
    assert switched["assistant_message"]["knowledge_base_id"] == kb_id
    assert kept["conversation"]["knowledge_base_id"] == kb_id  # omitted field keeps the selection
    assert back["assistant_message"]["answer_type"] == "general"


async def test_failed_answers_save_nothing(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM, db: AsyncSession
) -> None:
    first = await chat(client, alice, "How much annual leave?", knowledge_base_id=kb_id)
    llm.fail_with = LLMTimeoutError("The AI model took too long to respond. Please try again.")
    llm.rewrite = "annual leave unused days carry over"  # retrieves context, so the answer call runs

    failed_new = await client.post(CHAT, json={"message": "New topic"}, headers=alice)
    failed_follow_up = await client.post(
        CHAT,
        json={"message": "Do unused days carry over?", "conversation_id": first["conversation"]["id"]},
        headers=alice,
    )

    assert failed_new.status_code == failed_follow_up.status_code == 504
    assert failed_new.json()["error"]["code"] == "llm_timeout"
    assert await db.scalar(select(func.count()).select_from(Conversation)) == 1
    assert await db.scalar(select(func.count()).select_from(Message)) == 2


async def test_missing_api_key_rejects_before_saving(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    def not_configured():
        raise LLMNotConfiguredError("The AI model is not configured. Set LLM_API_KEY in .env and restart.")

    monkeypatch.setattr(llm_factory, "get_llm_provider", not_configured)

    response = await client.post(CHAT, json={"message": "Hi"}, headers=alice)

    assert response.status_code == 503
    assert await db.scalar(select(func.count()).select_from(Conversation)) == 0


async def test_listing_orders_by_recent_activity_with_previews(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    llm.answer = "Answer one."
    older = await chat(client, alice, "First conversation")
    llm.answer = "Answer two."
    await chat(client, alice, "Second conversation")
    llm.answer = "A later reply."
    await chat(client, alice, "Follow-up", conversation_id=older["conversation"]["id"])

    listing = (await client.get(CONVERSATIONS, headers=alice)).json()

    assert [c["title"] for c in listing] == ["First conversation", "Second conversation"]
    assert listing[0]["last_message_preview"] == "A later reply."
    assert listing[0]["message_count"] == 4


async def test_rename_change_kb_and_delete(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    conversation_id = (await chat(client, alice, "Hello"))["conversation"]["id"]

    renamed = await client.patch(
        f"{CONVERSATIONS}/{conversation_id}", json={"title": "  My  chat "}, headers=alice
    )
    rekb = await client.patch(
        f"{CONVERSATIONS}/{conversation_id}", json={"knowledge_base_id": kb_id}, headers=alice
    )
    deleted = await client.delete(f"{CONVERSATIONS}/{conversation_id}", headers=alice)

    assert renamed.json()["title"] == "My chat"
    assert rekb.json()["knowledge_base_name"] == "Handbook"
    assert deleted.status_code == 204
    assert (await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=alice)).status_code == 404


async def test_deleting_the_knowledge_base_keeps_the_conversation(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.cite = [(0, "employees receive 18 days")]
    conversation_id = (await chat(client, alice, "How much annual leave?", knowledge_base_id=kb_id))[
        "conversation"
    ]["id"]

    await client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=alice)
    detail = (await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=alice)).json()

    assert detail["knowledge_base_id"] is None
    source = detail["messages"][1]["sources"][0]
    assert source["chunk_id"] is None and source["document_id"] is None  # links cleared...
    assert source["filename"] == "leave.txt" and source["content"].startswith(
        "Annual leave"
    )  # ...snapshot kept


async def test_conversations_are_private(
    client: AsyncClient, alice: dict, kb_id: str, register_user: RegisterFn
) -> None:
    conversation_id = (await chat(client, alice, "Private question"))["conversation"]["id"]
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])

    assert (await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=bob)).status_code == 404
    assert (
        await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"title": "x"}, headers=bob)
    ).status_code == 404
    assert (await client.delete(f"{CONVERSATIONS}/{conversation_id}", headers=bob)).status_code == 404
    assert (
        await client.post(CHAT, json={"message": "hi", "conversation_id": conversation_id}, headers=bob)
    ).status_code == 404
    assert (
        await client.post(CHAT, json={"message": "hi", "knowledge_base_id": kb_id}, headers=bob)
    ).status_code == 404
    assert (await client.get(CONVERSATIONS, headers=bob)).json() == []


@pytest.mark.parametrize("payload", [{"message": "   "}, {"message": "x" * 2001}, {}])
async def test_invalid_messages(client: AsyncClient, alice: dict, payload: dict) -> None:
    assert (await client.post(CHAT, json=payload, headers=alice)).status_code == 422


async def test_message_order_does_not_depend_on_clock_resolution(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole turn can finish within one clock tick (Windows advances the wall clock in
    ~1-16 ms steps); questions must still sort before their answers."""
    from datetime import UTC, datetime

    from app.services import chat_service

    frozen = datetime(2026, 1, 1, tzinfo=UTC)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(chat_service, "datetime", FrozenClock)

    conversation_id = (await chat(client, alice, "First question"))["conversation"]["id"]
    await chat(client, alice, "Second question", conversation_id=conversation_id)
    detail = (await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=alice)).json()

    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in detail["messages"][::2]] == ["First question", "Second question"]
    stamps = [datetime.fromisoformat(m["created_at"]) for m in detail["messages"]]
    assert stamps == sorted(stamps) and len(set(stamps)) == 4
