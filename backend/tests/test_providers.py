"""Provider layer: factories, the per-call `ai_call` record, and the provider catalog."""

import asyncio
import logging

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.ai_calls import AICallKind, InstrumentedProvider, llm_usage, unwrap
from app.core.config import RerankerProviderName, Settings, get_settings
from app.core.providers import describe_providers
from app.llm import factory as llm_factory
from app.llm.base import LLMNotConfiguredError, LLMTimeoutError, Message, VisionNotSupportedError
from app.llm.claude import ClaudeClient
from app.multimodal.speech import get_speech_provider
from app.multimodal.tts import get_tts_provider
from app.rag.embeddings import get_embedding_provider
from app.rag.reranking import PassthroughReranker, get_reranker
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM

SYSTEM = "/api/v1/system/providers"

# Imported at module load, before conftest swaps the factories for test doubles in each test.
REAL_FACTORIES = (
    llm_factory.get_llm_provider,
    get_embedding_provider,
    get_reranker,
    get_speech_provider,
    get_tts_provider,
)


def ai_calls(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "ai_call"]


def instrumented(llm: ScriptedLLM) -> InstrumentedProvider:
    return InstrumentedProvider(
        llm, kind=AICallKind.LLM, provider="scripted", model="test-llm", methods={"generate": llm_usage}
    )


# --- the ai_call record -----------------------------------------------------------------------


async def test_a_successful_call_is_logged_with_usage_and_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    llm = ScriptedLLM()

    response = await instrumented(llm).generate(system="S", messages=[Message.user("a private question")])

    assert response.text == llm.answer  # the wrapper is transparent
    [record] = ai_calls(caplog)
    assert (record.kind, record.provider, record.operation) == ("llm", "scripted", "generate")
    assert record.status == "ok"
    assert (record.input_tokens, record.output_tokens) == (100, 20)
    assert record.latency_ms >= 0
    assert "a private question" not in str(record.__dict__)


async def test_a_failed_call_is_logged_and_the_error_propagates(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    llm = ScriptedLLM()
    llm.fail_with = LLMTimeoutError("slow")

    with pytest.raises(LLMTimeoutError):
        await instrumented(llm).generate(system="S", messages=[])

    [record] = ai_calls(caplog)
    assert record.status == "error" and record.error_type == "LLMTimeoutError"
    assert record.levelno == logging.WARNING


async def test_a_cancelled_call_is_logged_as_cancelled(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    class Hanging:
        async def generate(self, **_: object) -> None:
            await asyncio.sleep(60)

    provider = InstrumentedProvider(
        Hanging(), kind=AICallKind.LLM, provider="p", model="m", methods={"generate": None}
    )
    task = asyncio.create_task(provider.generate())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [record] = ai_calls(caplog)
    assert record.status == "cancelled"


def test_other_attributes_are_the_wrapped_providers() -> None:
    llm = ScriptedLLM()
    provider = instrumented(llm)

    assert provider.model_name == "test-llm" and provider.supports_images is True
    assert unwrap(provider) is llm and unwrap(llm) is llm
    assert not hasattr(provider, "warm_up")  # API providers have nothing to preload


async def test_a_chat_turn_produces_an_ai_call_record(
    client: AsyncClient,
    register_user: RegisterFn,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(llm_factory, "get_llm_provider", lambda: instrumented(ScriptedLLM()))
    headers = bearer((await register_user())["access_token"])

    response = await client.post("/api/v1/chat", json={"message": "Hello there"}, headers=headers)

    assert response.status_code == 200
    [record] = ai_calls(caplog)
    assert record.kind == "llm" and record.status == "ok"
    assert record.request_id  # joins the record to the HTTP request's logs


# --- factories --------------------------------------------------------------------------------


@pytest.fixture
def real_factories():
    """Fresh caches around the test; building providers loads no model and calls no API."""
    for factory in REAL_FACTORIES:
        factory.cache_clear()
    yield REAL_FACTORIES
    for factory in REAL_FACTORIES:
        factory.cache_clear()


def test_every_factory_returns_an_instrumented_provider(
    real_factories: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("sk-ant-test"))
    monkeypatch.setattr(settings, "reranker_provider", RerankerProviderName.LOCAL)

    kinds = ["llm", "embedding", "rerank", "speech_to_text", "text_to_speech"]
    for factory, kind in zip(real_factories, kinds, strict=True):
        provider = factory()
        assert isinstance(provider, InstrumentedProvider), kind
        assert provider._kind.value == kind
    assert isinstance(unwrap(real_factories[0]()), ClaudeClient)


def test_the_llm_factory_requires_credentials(real_factories: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_api_key", None)

    with pytest.raises(LLMNotConfiguredError, match="LLM_API_KEY"):
        real_factories[0]()


def test_reranking_turned_off_is_not_instrumented(
    real_factories: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "reranker_provider", RerankerProviderName.NONE)

    assert isinstance(real_factories[2](), PassthroughReranker)


def test_ensure_vision() -> None:
    llm = ScriptedLLM()
    llm_factory.ensure_vision(llm)
    llm.supports_images = False
    with pytest.raises(VisionNotSupportedError):
        llm_factory.ensure_vision(llm)


# --- catalog ----------------------------------------------------------------------------------


def test_catalog_describes_every_capability_from_configuration() -> None:
    settings = Settings(_env_file=None, llm_api_key=None, reranker_provider="none")

    catalog = {info.capability: info for info in describe_providers(settings)}

    assert list(catalog) == ["llm", "vision", "embeddings", "reranking", "speech_to_text", "text_to_speech"]
    assert (catalog["llm"].provider, catalog["llm"].runs) == ("anthropic", "api")
    assert catalog["llm"].configured is False
    assert catalog["vision"].model == catalog["llm"].model and catalog["vision"].configured is False
    assert (catalog["embeddings"].runs, catalog["embeddings"].model) == ("local", "BAAI/bge-small-en-v1.5")
    assert (catalog["reranking"].runs, catalog["reranking"].model) == ("off", "")
    assert catalog["speech_to_text"].configured and catalog["text_to_speech"].model == "en_US-lessac-medium"

    remote = Settings(_env_file=None, llm_api_key="k", stt_provider="openai", tts_provider="openai")
    catalog = {info.capability: info for info in describe_providers(remote)}
    assert catalog["llm"].configured and catalog["vision"].configured
    assert (catalog["speech_to_text"].runs, catalog["speech_to_text"].configured) == ("api", False)
    assert catalog["text_to_speech"].model == "tts-1/alloy"


def test_catalog_reports_hosted_reranking_and_its_key() -> None:
    def reranking(**overrides):
        settings = Settings(_env_file=None, reranker_provider="voyage", **overrides)
        return next(info for info in describe_providers(settings) if info.capability == "reranking")

    without_key = reranking()
    assert (without_key.provider, without_key.model, without_key.runs) == ("voyage", "rerank-2.5", "api")
    assert without_key.configured is False
    assert reranking(reranker_api_key="k").configured is True


async def test_providers_endpoint_requires_sign_in_and_never_returns_secrets(
    client: AsyncClient, register_user: RegisterFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "llm_api_key", SecretStr("sk-ant-secret-value"))

    assert (await client.get(SYSTEM)).status_code == 401

    headers = bearer((await register_user())["access_token"])
    response = await client.get(SYSTEM, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "Mindora AI" and body["tagline"] == "Your knowledge. One intelligent AI."
    llm = body["providers"][0]
    assert llm["capability"] == "llm" and llm["configured"] is True
    assert "sk-ant-secret-value" not in response.text


def test_usage_extractors_report_sizes_not_content() -> None:
    from types import SimpleNamespace

    from app.core import ai_calls

    assert ai_calls.embedding_documents_usage((["a", "b", "c"],), {}, None) == {"items": 3}
    assert ai_calls.embedding_documents_usage((), {"texts": ["a"]}, None) == {"items": 1}
    assert ai_calls.embedding_query_usage(("q",), {}, None) == {"items": 1}
    assert ai_calls.rerank_usage(("q", [1, 2, 3], 2), {}, [1, 2]) == {"items": 3, "returned": 2}
    assert ai_calls.transcription_usage((), {}, SimpleNamespace(duration_seconds=3.14159)) == {
        "audio_seconds": 3.14
    }
    assert ai_calls.synthesis_usage(("Hello",), {}, b"mp3") == {"characters": 5, "audio_bytes": 3}
