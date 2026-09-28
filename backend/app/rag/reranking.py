"""Cross-encoder reranking of retrieved chunks.

Retrieval (bi-encoder + keywords) is fast but judges query and passage
separately. A cross-encoder reads them together and is markedly better at near
ties, e.g. a paraphrase ("vacation days" vs "annual leave") against a text that
merely shares a word ("days"). It is too slow to run over a whole collection,
so it re-orders only the top RERANK_CANDIDATES fused results.

Scores are raw logits (unbounded, higher = more relevant) and are not
comparable across models. They are used for ordering only, not for filtering:
measured on the sample handbook, unanswerable-but-on-topic questions score
above some genuinely relevant passages, so no score cut-off separates them.
"""

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.core.config import RerankerProviderName, get_settings
from app.rag.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankError(Exception):
    """The reranker failed. Callers may fall back to the retrieval order."""


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk: RetrievedChunk
    rerank_score: float | None  # None when reranking is disabled


class Reranker(Protocol):
    model_name: str

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RankedChunk]: ...


class PassthroughReranker:
    """Keeps the retrieval (RRF) order. Used when RERANKER_PROVIDER=none."""

    model_name = "none"

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RankedChunk]:
        return [RankedChunk(chunk, None) for chunk in chunks[:top_k]]


class LocalCrossEncoderReranker:
    def __init__(self, model_name: str, cache_dir: Path, batch_size: int = 32) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    started = time.perf_counter()
                    try:
                        self._model = TextCrossEncoder(self.model_name, cache_dir=str(self._cache_dir))
                    except Exception as exc:
                        logger.exception("reranker_load_failed", extra={"model": self.model_name})
                        raise RerankError("The reranking model could not be loaded.") from exc
                    logger.info(
                        "reranker_loaded",
                        extra={
                            "model": self.model_name,
                            "load_ms": round((time.perf_counter() - started) * 1000),
                        },
                    )
        return self._model

    def warm_up(self) -> None:
        self._load()

    def _score(self, query: str, texts: list[str]) -> list[float]:
        return [float(score) for score in self._load().rerank(query, texts, batch_size=self._batch_size)]

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RankedChunk]:
        if not chunks:
            return []
        try:
            scores = await run_in_threadpool(self._score, query, [chunk.content for chunk in chunks])
        except RerankError:
            raise
        except Exception as exc:
            raise RerankError("Reranking failed.") from exc
        ranked = sorted(zip(chunks, scores, strict=True), key=lambda pair: -pair[1])
        return [RankedChunk(chunk, score) for chunk, score in ranked[:top_k]]


async def rerank_or_fallback(
    reranker: Reranker, query: str, chunks: Sequence[RetrievedChunk], top_k: int
) -> tuple[list[RankedChunk], bool]:
    """Rerank, degrading gracefully to the retrieval order if the reranker fails.
    Returns (ranked chunks, whether reranking was applied)."""
    try:
        ranked = await reranker.rerank(query, chunks, top_k)
        return ranked, not isinstance(reranker, PassthroughReranker)
    except RerankError:
        logger.warning("rerank_failed_using_retrieval_order", exc_info=True)
        return [RankedChunk(chunk, None) for chunk in chunks[:top_k]], False


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.reranker_provider is RerankerProviderName.NONE:
        return PassthroughReranker()
    return LocalCrossEncoderReranker(settings.reranker_model, settings.model_cache_dir)
