import json
import math

import httpx
import pytest

from app.core.config import Settings
from app.models import EMBEDDING_COLUMN_DIMENSIONS
from app.rag.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    LocalEmbeddingProvider,
    VoyageEmbeddingProvider,
    build_embedding_provider,
    validate_embedding_settings,
)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- configuration -----------------------------------------------------------


def test_local_provider_defaults_match_the_vector_column() -> None:
    config = settings()

    assert config.embedding_model == "BAAI/bge-small-en-v1.5"
    assert config.embedding_dimensions == EMBEDDING_COLUMN_DIMENSIONS
    validate_embedding_settings(config, EMBEDDING_COLUMN_DIMENSIONS)
    assert isinstance(build_embedding_provider(config), LocalEmbeddingProvider)


def test_dimension_mismatch_fails_fast_with_instructions() -> None:
    config = settings(embedding_provider="voyage", embedding_api_key="key")  # voyage default: 1024 dims

    with pytest.raises(EmbeddingConfigurationError, match="does not match the database vector column"):
        validate_embedding_settings(config, EMBEDDING_COLUMN_DIMENSIONS)


def test_voyage_requires_an_api_key() -> None:
    config = settings(embedding_provider="voyage", embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS)

    with pytest.raises(EmbeddingConfigurationError, match="EMBEDDING_API_KEY"):
        validate_embedding_settings(config, EMBEDDING_COLUMN_DIMENSIONS)


def test_empty_env_values_mean_defaults() -> None:
    config = settings(embedding_model="  ", embedding_dimensions="", llm_api_key="")

    assert config.embedding_model == "BAAI/bge-small-en-v1.5"
    assert config.embedding_dimensions == 384
    assert config.llm_api_key is None


# --- Voyage (HTTP mocked) ----------------------------------------------------


def voyage(handler, *, batch_size: int = 2, dimensions: int = 3) -> VoyageEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VoyageEmbeddingProvider(
        api_key="test-key",
        model_name="voyage-3.5",
        dimensions=dimensions,
        batch_size=batch_size,
        timeout=5,
        client=client,
    )


async def test_voyage_batches_requests_and_orders_by_index() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"auth": request.headers["Authorization"], **body})
        # Return items out of order to prove we sort by "index".
        data = [
            {"index": i, "embedding": [float(len(text)), 0.0, 1.0]} for i, text in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": list(reversed(data)), "usage": {"total_tokens": 5}})

    vectors = await voyage(handler).embed_documents(["a", "bb", "ccc"])

    assert vectors == [[1.0, 0.0, 1.0], [2.0, 0.0, 1.0], [3.0, 0.0, 1.0]]
    assert [r["input"] for r in requests] == [["a", "bb"], ["ccc"]]
    assert requests[0] | {"input": None} == {
        "auth": "Bearer test-key",
        "input": None,
        "model": "voyage-3.5",
        "input_type": "document",
        "output_dimension": 3,
    }


async def test_voyage_query_uses_query_input_type() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["input_type"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    assert await voyage(handler).embed_query("question?") == [0.1, 0.2, 0.3]
    assert seen == ["query"]


async def test_voyage_retries_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.embeddings.asyncio.sleep", lambda _s: _noop())
    responses = iter([httpx.Response(429), httpx.Response(503), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response or httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]})

    assert await voyage(handler).embed_documents(["x"]) == [[1.0, 0.0, 0.0]]


async def _noop() -> None:
    return None


@pytest.mark.parametrize(
    ("status", "message"), [(401, "rejected the API key"), (400, "returned an error \\(400\\)")]
)
async def test_voyage_client_errors_are_not_retried(status: int, message: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": "nope"})

    with pytest.raises(EmbeddingError, match=message):
        await voyage(handler).embed_documents(["x"])
    assert calls == 1


async def test_wrong_dimensions_from_the_api_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    with pytest.raises(EmbeddingError, match="returned 2 dimensions, expected 3"):
        await voyage(handler).embed_documents(["x"])


# --- real local model --------------------------------------------------------


@pytest.mark.model
async def test_local_model_produces_normalised_vectors_that_rank_relevant_text_higher() -> None:
    config = settings()
    provider = LocalEmbeddingProvider(
        config.embedding_model, config.embedding_dimensions, config.model_cache_dir, batch_size=8
    )
    documents = [
        "Employees receive 18 days of annual paid leave.",
        "The cafeteria serves lunch from noon until 2pm.",
    ]

    vectors = await provider.embed_documents(documents)
    query = await provider.embed_query("How many vacation days do I get?")

    assert all(len(v) == EMBEDDING_COLUMN_DIMENSIONS for v in vectors)
    assert all(math.isclose(math.hypot(*v), 1.0, rel_tol=1e-3) for v in vectors)
    relevant, irrelevant = (sum(a * b for a, b in zip(query, v, strict=True)) for v in vectors)
    assert relevant > irrelevant + 0.1
