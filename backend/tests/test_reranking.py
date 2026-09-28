import uuid

import pytest

from app.core.config import get_settings
from app.rag.reranking import (
    LocalCrossEncoderReranker,
    PassthroughReranker,
    RerankError,
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
