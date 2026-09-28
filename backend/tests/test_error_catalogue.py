"""Every failure the spec lists, through the real API: a stable error code, a message a
user can act on, and nothing internal (stack traces, SQL, provider responses, secrets).

Deeper tests for each case live next to the feature; this file is the checklist.
"""

import io
import logging
import wave

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.llm.claude import LLMTimeoutError, LLMUnavailableError
from app.multimodal.speech import SpeechToTextError
from app.multimodal.tts import TextToSpeechError
from app.rag import embeddings
from app.rag.embeddings import EmbeddingError
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM, ScriptedSpeech, ScriptedTTS
from tests.ws_client import websocket

pytestmark = pytest.mark.integration

API = "/api/v1"
INTERNALS = ("Traceback", "sqlalchemy", "asyncpg", 'File "', "SELECT ", "sk-ant", "Exception")


class FailingEmbedder:
    model_name = "failing"
    dimensions = 384

    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingError("The embedding service could not be reached.")

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("The embedding service could not be reached.")


def one_second_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x10\x00" * 16_000)
    return buffer.getvalue()


def assert_friendly(response, status: int, code: str, message: str) -> None:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert message in error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    for internal in INTERNALS:
        assert internal not in response.text


@pytest.fixture
async def user(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user())["access_token"])


@pytest.fixture
async def kb(client: AsyncClient, user: dict) -> str:
    return (await client.post(f"{API}/knowledge-bases", json={"name": "KB"}, headers=user)).json()["id"]


async def test_invalid_login(client: AsyncClient, user: dict) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )
    assert_friendly(response, 401, "unauthorized", "Invalid email or password")


async def test_unauthorized_request(client: AsyncClient) -> None:
    assert_friendly(
        await client.get(f"{API}/knowledge-bases"), 401, "unauthorized", "Authentication is required"
    )


async def test_missing_knowledge_base(client: AsyncClient, user: dict) -> None:
    response = await client.get(f"{API}/knowledge-bases/00000000-0000-0000-0000-000000000000", headers=user)
    assert_friendly(response, 404, "not_found", "Knowledge base not found")


async def test_invalid_document(client: AsyncClient, user: dict, kb: str) -> None:
    response = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": ("report.pdf", b"not a pdf")},
        headers=user,
    )
    assert_friendly(response, 422, "invalid_document", "is not a PDF document")


async def test_unsupported_file(client: AsyncClient, user: dict, kb: str) -> None:
    response = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": ("setup.exe", b"MZ...")},
        headers=user,
    )
    assert_friendly(response, 415, "unsupported_file_type", "not supported")


async def test_failed_document_processing_is_reported_on_the_document(
    client: AsyncClient, user: dict, kb: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.ingestion_service import process_document

    document = (
        await client.post(
            f"{API}/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": ("a.txt", b"Text.")},
            headers=user,
        )
    ).json()
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: FailingEmbedder())
    import uuid

    await process_document(uuid.UUID(document["id"]))

    detail = (await client.get(f"{API}/documents/{document['id']}", headers=user)).json()
    assert detail["status"] == "failed"
    assert detail["error_message"] == "The embedding service could not be reached."


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/retrieval/search", {"query": "leave"}),
        ("POST", "/rag/answer", {"question": "leave?"}),
        ("POST", "/chat", {"message": "leave?"}),
    ],
    ids=["search", "rag-answer", "chat"],
)
async def test_embedding_failure_at_query_time(
    client: AsyncClient,
    user: dict,
    kb: str,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict,
) -> None:
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: FailingEmbedder())
    key = "knowledge_base_id" if path == "/chat" else "knowledge_base_ids"
    payload = {**body, key: kb if path == "/chat" else [kb]}

    response = await client.request(method, f"{API}{path}", json=payload, headers=user)

    assert_friendly(response, 502, "embedding_error", "embedding service could not be reached")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (
            LLMUnavailableError("The AI model is temporarily unavailable. Please try again."),
            503,
            "llm_unavailable",
        ),
        (LLMTimeoutError("The AI model took too long to respond. Please try again."), 504, "llm_timeout"),
    ],
    ids=["llm-unavailable", "llm-timeout"],
)
async def test_llm_failure_and_timeout(
    client: AsyncClient, user: dict, llm: ScriptedLLM, error: Exception, status: int, code: str
) -> None:
    llm.fail_with = error
    response = await client.post(f"{API}/chat", json={"message": "hi"}, headers=user)
    assert_friendly(response, status, code, "Please try again")


async def test_speech_to_text_failure(
    client: AsyncClient, user: dict, stt: ScriptedSpeech, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args, **kwargs):
        raise SpeechToTextError("Speech recognition failed. Please try again.")

    monkeypatch.setattr(stt, "transcribe", fail)
    response = await client.post(
        f"{API}/voice/transcribe", files={"file": ("a.wav", one_second_wav())}, headers=user
    )
    assert_friendly(response, 502, "stt_error", "Speech recognition failed")


async def test_text_to_speech_failure(client: AsyncClient, user: dict, tts_provider: ScriptedTTS) -> None:
    tts_provider.error = TextToSpeechError()
    response = await client.post(f"{API}/voice/synthesize", json={"text": "Hello."}, headers=user)
    assert_friendly(response, 502, "tts_error", "Speech could not be generated")


async def test_image_processing_failure(client: AsyncClient, user: dict) -> None:
    response = await client.post(
        f"{API}/images", files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nbroken", "image/png")}, headers=user
    )
    assert_friendly(response, 422, "invalid_image", "not a valid image or is damaged")


async def test_websocket_disconnect_mid_answer_saves_nothing_and_logs_it(
    app: FastAPI,
    client: AsyncClient,
    register_user: RegisterFn,
    llm: ScriptedLLM,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    token = (await register_user(email="ws@example.com"))["access_token"]
    llm.gate = asyncio.Event()
    caplog.set_level(logging.INFO)

    async with websocket(app, token=token) as ws:
        await ws.send_json({"type": "chat", "id": "t", "message": "Hello"})
        await ws.receive_until("delta")
        await ws.disconnect()

    assert (await client.get(f"{API}/conversations", headers=bearer(token))).json() == []
    assert any(r.getMessage() == "ws_disconnected" for r in caplog.records)


# --- startup and shutdown -------------------------------------------------------------------------


@pytest.fixture
def quiet_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup warms up local models; the fakes are not local models, so nothing loads."""
    import app.main as main
    from tests.fakes import HashingEmbeddingProvider

    monkeypatch.setattr(main, "get_embedding_provider", HashingEmbeddingProvider)
    monkeypatch.setattr(main, "get_reranker", lambda: None)
    monkeypatch.setattr(main, "get_speech_provider", lambda: None)
    monkeypatch.setattr(main, "get_tts_provider", lambda: None)


async def test_startup_and_shutdown(quiet_models: None, caplog: pytest.LogCaptureFixture) -> None:
    from app.main import create_app
    from app.workers.job_queue import JobQueue

    caplog.set_level(logging.INFO)
    application = create_app()

    async with application.router.lifespan_context(application):
        assert isinstance(application.state.ingestion_queue, JobQueue)

    messages = [r.getMessage() for r in caplog.records]
    assert "application_startup" in messages
    assert "llm_not_configured" in messages  # no key in tests: a clear hint at startup
    assert messages[-1] == "application_shutdown"


async def test_startup_fails_fast_on_a_misconfigured_embedding_model(
    quiet_models: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.main import create_app
    from app.rag.embeddings import EmbeddingConfigurationError

    monkeypatch.setattr(get_settings(), "embedding_dimensions", 768)
    application = create_app()

    with pytest.raises(EmbeddingConfigurationError, match="does not match the database vector column"):
        async with application.router.lifespan_context(application):
            pass
