"""Cross-encoder reranking of retrieved chunks.

Retrieval (bi-encoder + keywords) is fast but judges query and passage
separately. A cross-encoder reads them together and is markedly better at near
ties, e.g. a paraphrase ("vacation days" vs "annual leave") against a text that
merely shares a word ("days"). It is too slow to run over a whole collection,
so it re-orders only the top RERANK_CANDIDATES fused results.

Providers (RERANKER_PROVIDER) implement the `Reranker` protocol: `local` (a
fastembed cross-encoder on this machine), `voyage` (Voyage AI's hosted rerank
API) and `none` (keep the retrieval order). The pipeline never names a vendor.

Scores are whatever the model returns: raw logits for the local cross-encoder
(unbounded, higher = more relevant), 0..1 for Voyage. They are not comparable
across models and are never rescaled or invented; when reranking is off or
fails, the score is None. They are used for ordering, not filtering by default:
measured on the sample handbook, unanswerable-but-on-topic questions score
above some genuinely relevant passages, so no score cut-off separates them.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Protocol, cast

import httpx
from starlette.concurrency import run_in_threadpool

from app.core.ai_calls import AICallKind, InstrumentedProvider, rerank_usage, unwrap
from app.core.config import RerankerProviderName, Settings, get_settings
from app.rag.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankError(Exception):
    """The reranker failed. Callers may fall back to the retrieval order."""


class RerankerConfigurationError(RuntimeError):
    """Invalid reranker configuration; raised at startup so the app fails fast."""


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
        return ranked, not isinstance(unwrap(reranker), PassthroughReranker)
    except RerankError:
        logger.warning("rerank_failed_using_retrieval_order", exc_info=True)
        return [RankedChunk(chunk, None) for chunk in chunks[:top_k]], False


class VoyageReranker:
    """Voyage AI rerank API (https://docs.voyageai.com/reference/reranker-api).

    `relevance_score` is the model's own score (0..1 for current models); it is passed through
    unchanged, never rescaled or invented. Any failure raises RerankError, so the pipeline
    keeps the retrieval order and reports the answer as not reranked."""

    URL = "https://api.voyageai.com/v1/rerank"
    MAX_DOCUMENTS = 1000  # API limit per request
    _RETRY_STATUSES: ClassVar[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
        *,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key
        self._max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def _post(self, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(self.URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self._max_attempts:
                    raise RerankError("The reranking service could not be reached.") from exc
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise RerankError("The reranking service returned an invalid response.") from exc
                if response.status_code not in self._RETRY_STATUSES or attempt == self._max_attempts:
                    # The body is the API's error message; it never contains the query or passages.
                    logger.error(
                        "rerank_api_error",
                        extra={"status_code": response.status_code, "body": response.text[:500]},
                    )
                    if response.status_code in (401, 403):
                        raise RerankError("The reranking service rejected the API key.")
                    raise RerankError(f"The reranking service returned an error ({response.status_code}).")
            await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RankedChunk]:
        if not chunks:
            return []
        chunks = chunks[: self.MAX_DOCUMENTS]
        body = await self._post(
            {
                "query": query,
                "documents": [chunk.content for chunk in chunks],
                "model": self.model_name,
                "top_k": min(top_k, len(chunks)),
                "truncation": True,
            }
        )
        try:
            scored = [(int(item["index"]), float(item["relevance_score"])) for item in body["data"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError("The reranking service returned an invalid response.") from exc
        if any(not 0 <= index < len(chunks) for index, _ in scored) or len({i for i, _ in scored}) != len(
            scored
        ):
            raise RerankError("The reranking service returned an invalid response.")
        scored.sort(key=lambda pair: -pair[1])
        return [RankedChunk(chunks[index], score) for index, score in scored[:top_k]]


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_provider is RerankerProviderName.NONE:
        return PassthroughReranker()
    if settings.reranker_provider is RerankerProviderName.VOYAGE:
        if settings.reranker_api_key is None:
            raise RerankerConfigurationError("RERANKER_PROVIDER=voyage requires RERANKER_API_KEY to be set.")
        return VoyageReranker(
            api_key=settings.reranker_api_key.get_secret_value(),
            model_name=settings.reranker_model,
            timeout=settings.reranker_timeout_seconds,
        )
    return LocalCrossEncoderReranker(settings.reranker_model, settings.model_cache_dir)


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    reranker = build_reranker(settings)
    if isinstance(reranker, PassthroughReranker):
        return reranker
    wrapped = InstrumentedProvider(
        reranker,
        kind=AICallKind.RERANK,
        provider=settings.reranker_provider.value,
        model=reranker.model_name,
        methods={"rerank": rerank_usage},
    )
    return cast(Reranker, wrapped)
