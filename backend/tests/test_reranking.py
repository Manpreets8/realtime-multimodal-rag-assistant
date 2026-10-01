import asyncio
import json
import uuid

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.rag.reranking import (
    LocalCrossEncoderReranker,
    PassthroughReranker,
    RerankerConfigurationError,
    RerankError,
    VoyageReranker,
    build_reranker,
    rerank_or_fallback,
)
from app.rag.retrieval import RetrievedChunk


def chunk(content: str, index: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        filename=f"doc{index}.txt",
        chunk_index=index,
        page_number=None,
        section=None,
        content=content,
        score=1.0 / (index + 1),
        similarity=0.6,
        keyword_score=None,
        vector_rank=index + 1,
        keyword_rank=None,
    )


class FakeCrossEncoder:
    """Scores = number of query words present in the passage."""

    def rerank(self, query: str, documents, batch_size: int = 32):
        words = set(query.lower().split())
        return [float(sum(word in document.lower() for word in words)) for document in documents]


def local_with(model) -> LocalCrossEncoderReranker:
    reranker = LocalCrossEncoderReranker("fake", cache_dir=None)  # type: ignore[arg-type]
    reranker._model = model
    return reranker


async def test_cross_encoder_reorders_and_truncates() -> None:
    chunks = [
        chunk("cafeteria hours", 0),
        chunk("annual leave days per year", 1),
        chunk("leave carry over", 2),
    ]

    ranked = await local_with(FakeCrossEncoder()).rerank("annual leave days", chunks, top_k=2)

    assert [r.chunk.content for r in ranked] == ["annual leave days per year", "leave carry over"]
    assert [r.rerank_score for r in ranked] == [3.0, 1.0]


async def test_passthrough_keeps_retrieval_order() -> None:
    chunks = [chunk("a", 0), chunk("b", 1), chunk("c", 2)]

    ranked, applied = await rerank_or_fallback(PassthroughReranker(), "q", chunks, top_k=2)

    assert [r.chunk.content for r in ranked] == ["a", "b"]
    assert all(r.rerank_score is None for r in ranked)
    assert applied is False


async def test_reranker_failure_falls_back_to_retrieval_order() -> None:
    class Broken:
        def rerank(self, *args, **kwargs):
            raise RuntimeError("onnx exploded")

    chunks = [chunk("a", 0), chunk("b", 1)]

    ranked, applied = await rerank_or_fallback(local_with(Broken()), "q", chunks, top_k=5)

    assert [r.chunk.content for r in ranked] == ["a", "b"]
    assert applied is False


async def test_empty_input() -> None:
    assert await local_with(FakeCrossEncoder()).rerank("q", [], top_k=5) == []


async def test_errors_are_wrapped() -> None:
    class Broken:
        def rerank(self, *args, **kwargs):
            raise ValueError("bad input")

    with pytest.raises(RerankError):
        await local_with(Broken()).rerank("q", [chunk("a")], top_k=1)


@pytest.mark.model
async def test_real_cross_encoder_fixes_the_paraphrase_near_tie() -> None:
    """The case bge-small got wrong in Phase 5: "vacation days" vs a text that only shares "days"."""
    settings = get_settings()
    reranker = LocalCrossEncoderReranker(settings.reranker_model, settings.model_cache_dir)
    travel = chunk("Business meals are reimbursed up to 60 USD per day. Submit receipts within 30 days.", 0)
    leave = chunk(
        "All full-time employees are entitled to 18 days of paid annual leave per calendar year.", 1
    )

    ranked = await reranker.rerank("How many vacation days do I get?", [travel, leave], top_k=2)

    assert ranked[0].chunk is leave
    assert ranked[0].rerank_score > ranked[1].rerank_score


# --- Voyage (hosted) ---------------------------------------------------------------------------


def voyage(handler, *, max_attempts: int = 3) -> VoyageReranker:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VoyageReranker("test-key", "rerank-2.5", timeout=5, max_attempts=max_attempts, client=client)


async def test_voyage_sends_the_passages_and_returns_its_own_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    chunks = [chunk("hotel limits", 0), chunk("annual leave is 18 days", 1), chunk("leave carry over", 2)]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"auth": request.headers["Authorization"], **json.loads(request.content)})
        # Deliberately not sorted by score, to prove the client orders them.
        data = [{"index": 2, "relevance_score": 0.41}, {"index": 1, "relevance_score": 0.87}]
        return httpx.Response(
            200, json={"object": "list", "data": data, "model": "rerank-2.5", "usage": {"total_tokens": 30}}
        )

    ranked = await voyage(handler).rerank("annual leave", chunks, top_k=2)

    assert [(r.chunk.content, r.rerank_score) for r in ranked] == [
        ("annual leave is 18 days", 0.87),
        ("leave carry over", 0.41),
    ]
    assert seen == [
        {
            "auth": "Bearer test-key",
            "query": "annual leave",
            "documents": ["hotel limits", "annual leave is 18 days", "leave carry over"],
            "model": "rerank-2.5",
            "top_k": 2,
            "truncation": True,
        }
    ]


async def _no_sleep(_: float) -> None:
    return None


async def test_voyage_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status != 200:
            return httpx.Response(status, json={"detail": "busy"})
        return httpx.Response(200, json={"data": [{"index": 0, "relevance_score": 0.5}]})

    ranked = await voyage(handler).rerank("q", [chunk("a")], top_k=1)

    assert [r.rerank_score for r in ranked] == [0.5]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={"detail": "bad key"}), "rejected the API key"),
        (httpx.Response(400, json={"detail": "bad model"}), r"returned an error \(400\)"),
        (httpx.Response(200, json={"data": [{"index": 7, "relevance_score": 0.9}]}), "invalid response"),
        (httpx.Response(200, json={"data": [{"index": 0}]}), "invalid response"),
        (httpx.Response(200, content=b"not json"), "invalid response"),
    ],
)
async def test_voyage_errors_raise_rerank_error(response: httpx.Response, message: str) -> None:
    with pytest.raises(RerankError, match=message):
        await voyage(lambda request: response).rerank("q", [chunk("a"), chunk("b", 1)], top_k=2)


async def test_a_failed_hosted_reranker_never_invents_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    chunks = [chunk("first", 0), chunk("second", 1)]

    ranked, applied = await rerank_or_fallback(
        voyage(lambda request: httpx.Response(500), max_attempts=2), "q", chunks, 2
    )

    assert applied is False
    assert [(r.chunk, r.rerank_score) for r in ranked] == [(chunks[0], None), (chunks[1], None)]


def test_build_reranker_per_provider() -> None:
    def settings(**overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    assert isinstance(build_reranker(settings(reranker_provider="none")), PassthroughReranker)
    local = build_reranker(settings(reranker_provider="local"))
    assert (
        isinstance(local, LocalCrossEncoderReranker) and local.model_name == "Xenova/ms-marco-MiniLM-L-6-v2"
    )
    hosted = build_reranker(settings(reranker_provider="voyage", reranker_api_key="k"))
    assert isinstance(hosted, VoyageReranker) and hosted.model_name == "rerank-2.5"
    custom = build_reranker(
        settings(reranker_provider="voyage", reranker_api_key="k", reranker_model="rerank-2.5-lite")
    )
    assert custom.model_name == "rerank-2.5-lite"
    with pytest.raises(RerankerConfigurationError, match="RERANKER_API_KEY"):
        build_reranker(settings(reranker_provider="voyage", reranker_api_key=""))
    assert settings(reranker_provider="none", reranker_model="anything").reranker_model == ""
