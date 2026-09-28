"""Temporary processing state and caches kept in Redis."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag import embeddings
from app.rag.embeddings import embed_query_cached
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import HashingEmbeddingProvider

pytestmark = pytest.mark.integration


class GatedEmbedder(HashingEmbeddingProvider):
    """Pauses in the middle of embedding so a test can look at progress."""

    def __init__(self) -> None:
        super().__init__()
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.batches = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches += 1
        if self.batches == 2:
            self.reached.set()
            await self.release.wait()
        return await super().embed_documents(texts)


class CountingEmbedder(HashingEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.queries = 0

    async def embed_query(self, text: str) -> list[float]:
        self.queries += 1
        return await super().embed_query(text)


async def test_live_ingestion_progress_is_reported_then_cleared(
    client: AsyncClient,
    register_user: RegisterFn,
    redis_db: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = bearer((await register_user())["access_token"])
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=headers)).json()["id"]
    text = "\n\n".join(f"Paragraph {n}: " + "policy details " * 60 for n in range(12)).encode()
    document_id = (
        await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": ("long.txt", text)},
            headers=headers,
        )
    ).json()["id"]
    embedder = GatedEmbedder()
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: embedder)
    monkeypatch.setattr(get_settings(), "embedding_batch_size", 4)

    task = asyncio.create_task(process_document(uuid.UUID(document_id)))
    await asyncio.wait_for(embedder.reached.wait(), 10)

    detail = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
    listed = (await client.get(f"/api/v1/knowledge-bases/{kb}/documents", headers=headers)).json()
    embedder.release.set()
    await asyncio.wait_for(task, 10)
    done = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()

    assert detail["status"] == "processing"
    progress = detail["progress"]
    assert progress["stage"] == "embedding"
    assert progress["done"] == 4 and progress["total"] > 8
    assert listed[0]["progress"] == progress
    assert done["status"] == "completed" and done["progress"] is None
    assert await redis_db.keys("rag:ingestion:progress:*") == []


async def test_query_embeddings_are_cached(redis_db: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CountingEmbedder()

    first, first_hit = await embed_query_cached(provider, "annual leave policy")
    second, second_hit = await embed_query_cached(provider, "annual leave policy")
    await embed_query_cached(provider, "remote work")

    assert (first_hit, second_hit) == (False, True)
    assert second == pytest.approx(first, abs=1e-6)  # float32 round trip
    assert provider.queries == 2
    ttl = await redis_db.ttl(next(iter(await redis_db.keys("rag:cache:query-embedding:*"))))
    assert 0 < ttl <= get_settings().query_embedding_cache_ttl_seconds


async def test_query_embedding_cache_is_per_model_and_can_be_disabled(
    redis_db: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = CountingEmbedder(), CountingEmbedder()
    b.model_name = "another-model"
    await embed_query_cached(a, "question")
    _, hit_other_model = await embed_query_cached(b, "question")
    monkeypatch.setattr(get_settings(), "query_embedding_cache_ttl_seconds", 0)
    _, hit_disabled = await embed_query_cached(a, "question")

    assert hit_other_model is False  # never another model's vectors
    assert hit_disabled is False
    assert (a.queries, b.queries) == (2, 1)


async def test_retrieval_uses_the_cache(
    client: AsyncClient, register_user: RegisterFn, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingEmbedder()
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: provider)
    headers = bearer((await register_user())["access_token"])
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=headers)).json()["id"]
    query = {"query": "annual leave", "knowledge_base_ids": [kb]}

    for _ in range(3):
        response = await client.post("/api/v1/retrieval/search", json=query, headers=headers)
        assert response.status_code == 200, response.text

    assert provider.queries == 1
