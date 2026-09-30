"""Embedding providers.

`EmbeddingProvider` is the only interface the rest of the app uses. Two
implementations are provided and selected with EMBEDDING_PROVIDER:

- `local`:  fastembed (ONNX Runtime, no PyTorch) running BAAI/bge-small-en-v1.5
            by default. No API key; the model (~65 MB) downloads once to MODEL_CACHE_DIR.
- `voyage`: Voyage AI's hosted embeddings API.

Documents and queries are embedded differently (asymmetric retrieval): both
providers expose `embed_documents` and `embed_query`.
"""

import asyncio
import logging
import threading
import time
from array import array
from collections.abc import Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Protocol, cast

import httpx
from starlette.concurrency import run_in_threadpool

from app.core import cache
from app.core.ai_calls import (
    AICallKind,
    InstrumentedProvider,
    embedding_documents_usage,
    embedding_query_usage,
)
from app.core.config import EmbeddingProviderName, Settings, get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)


class EmbeddingError(AppError):
    """The embedding backend failed. The message is safe to show to users: during ingestion it
    becomes the document's failure reason; during a search or answer it is a 502 response."""

    status_code = 502
    code = "embedding_error"
    message = "The embedding service is unavailable. Please try again."


class EmbeddingConfigurationError(RuntimeError):
    """Invalid embedding configuration; raised at startup so the app fails fast."""


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def _batched(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    # itertools.batched is Python 3.12+; the project supports 3.11.
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _check_dimensions(vectors: list[list[float]], expected: int, model: str) -> list[list[float]]:
    for vector in vectors:
        if len(vector) != expected:
            raise EmbeddingError(
                f"The embedding model '{model}' returned {len(vector)} dimensions, expected {expected}."
            )
    return vectors


class LocalEmbeddingProvider:
    def __init__(self, model_name: str, dimensions: int, cache_dir: Path, batch_size: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding  # heavy import; only when used

                    started = time.perf_counter()
                    try:
                        self._model = TextEmbedding(self.model_name, cache_dir=str(self._cache_dir))
                    except Exception as exc:
                        logger.exception("embedding_model_load_failed", extra={"model": self.model_name})
                        raise EmbeddingError(
                            "The local embedding model could not be loaded. Check the server logs."
                        ) from exc
                    logger.info(
                        "embedding_model_loaded",
                        extra={
                            "model": self.model_name,
                            "load_ms": round((time.perf_counter() - started) * 1000),
                        },
                    )
        return self._model

    def warm_up(self) -> None:
        """Load the model ahead of the first request (blocking)."""
        self._load()

    def _embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = [vector.tolist() for vector in model.passage_embed(texts, batch_size=self._batch_size)]
        return _check_dimensions(vectors, self.dimensions, self.model_name)

    def _embed_query_sync(self, text: str) -> list[float]:
        model = self._load()
        vectors = [vector.tolist() for vector in model.query_embed(text)]
        return _check_dimensions(vectors, self.dimensions, self.model_name)[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await run_in_threadpool(self._embed_documents_sync, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        return await run_in_threadpool(self._embed_query_sync, text)


class VoyageEmbeddingProvider:
    """Voyage AI REST API (https://docs.voyageai.com/reference/embeddings-api)."""

    URL = "https://api.voyageai.com/v1/embeddings"
    _RETRY_STATUSES: ClassVar[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        api_key: str,
        model_name: str,
        dimensions: int,
        batch_size: int,
        timeout: float,
        *,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._api_key = api_key
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def _post(self, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(self.URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self._max_attempts:
                    raise EmbeddingError("The embedding service could not be reached.") from exc
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code not in self._RETRY_STATUSES or attempt == self._max_attempts:
                    logger.error(
                        "embedding_api_error",
                        extra={"status_code": response.status_code, "body": response.text[:500]},
                    )
                    if response.status_code in (401, 403):
                        raise EmbeddingError("The embedding service rejected the API key.")
                    raise EmbeddingError(f"The embedding service returned an error ({response.status_code}).")
            await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    async def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            body = await self._post(
                {
                    "input": list(batch),
                    "model": self.model_name,
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                }
            )
            data = sorted(body["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in data)
        return _check_dimensions(vectors, self.dimensions, self.model_name)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "document") if texts else []

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query"))[0]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider is EmbeddingProviderName.VOYAGE:
        if settings.embedding_api_key is None:
            raise EmbeddingConfigurationError(
                "EMBEDDING_PROVIDER=voyage requires EMBEDDING_API_KEY to be set."
            )
        return VoyageEmbeddingProvider(
            api_key=settings.embedding_api_key.get_secret_value(),
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            timeout=settings.embedding_timeout_seconds,
        )
    return LocalEmbeddingProvider(
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        cache_dir=settings.model_cache_dir,
        batch_size=settings.embedding_batch_size,
    )


def validate_embedding_settings(settings: Settings, column_dimensions: int) -> None:
    """Fail at startup, with instructions, if the vector column can't hold this model's vectors."""
    if settings.embedding_dimensions != column_dimensions:
        raise EmbeddingConfigurationError(
            f"EMBEDDING_DIMENSIONS={settings.embedding_dimensions} ({settings.embedding_provider.value}: "
            f"{settings.embedding_model}) does not match the database vector column "
            f"({column_dimensions}). Use a model with {column_dimensions} dimensions, or add a "
            "migration that changes the column size and re-process all documents."
        )
    build_embedding_provider(settings)  # surfaces missing API keys etc.


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = build_embedding_provider(settings)
    wrapped = InstrumentedProvider(
        provider,
        kind=AICallKind.EMBEDDING,
        provider=settings.embedding_provider.value,
        model=provider.model_name,
        methods={"embed_documents": embedding_documents_usage, "embed_query": embedding_query_usage},
    )
    return cast(EmbeddingProvider, wrapped)


async def embed_query_cached(provider: EmbeddingProvider, text: str) -> tuple[list[float], bool]:
    """`provider.embed_query` through the Redis cache: (vector, cache_hit).

    Worth it mainly for API providers (a network round trip and a per-token charge per
    query); repeated questions, suggested prompts and evaluation runs hit the cache.
    Keyed by model and dimensions, so changing the model never returns stale vectors."""
    ttl = get_settings().query_embedding_cache_ttl_seconds
    if ttl <= 0:
        return await provider.embed_query(text), False
    key = cache.cache_key("query-embedding", provider.model_name, str(provider.dimensions), text)
    stored = await cache.get_bytes(key)
    if stored is not None:
        return array("f", stored).tolist(), True
    vector = await provider.embed_query(text)
    await cache.set_bytes(key, array("f", vector).tobytes(), ttl)
    return vector, False
