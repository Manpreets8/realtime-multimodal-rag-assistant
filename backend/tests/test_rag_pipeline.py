"""The advanced pipeline stages: metadata filters, deduplication, the rerank threshold, context
selection and citation validation, plus the per-answer record of what each stage did."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.base import CitationSpan
from app.models import Document
from app.rag import reranking
from app.rag.context import deduplicate, select_context
from app.rag.pipeline import check_citations, map_citations
from app.rag.reranking import RankedChunk
from app.rag.retrieval import RetrievedChunk
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.integration

API = "/api/v1"


def chunk(content: str, score: float = 0.03) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        filename="doc.txt",
        chunk_index=0,
        page_number=None,
        section=None,
        content=content,
        score=score,
        similarity=0.7,
        keyword_score=None,
        vector_rank=1,
        keyword_rank=None,
    )


LEAVE = (
    "Annual leave: employees receive 18 days of paid annual leave each year, "
    "accrued monthly from the start date."
)


# --- deduplication and context selection (pure) -------------------------------------------------


def test_near_duplicates_are_removed_keeping_the_better_ranked_copy() -> None:
    first = chunk(LEAVE, score=0.05)
    copy = chunk(LEAVE.replace("each year", "each  year"), score=0.04)  # whitespace differences only
    edited = chunk(LEAVE.replace("accrued monthly from the start date", "accrued monthly"), score=0.035)
    other = chunk(
        "Remote work: staff may work remotely three days per week with manager approval.", score=0.03
    )

    kept, removed = deduplicate([first, copy, edited, other], 0.9)

    assert kept == [first, edited, other] and removed == 1  # the light edit is still different enough
    assert deduplicate([first, copy], None) == ([first, copy], 0)


def test_adjacent_overlapping_chunks_are_not_duplicates() -> None:
    words = [f"word{i}" for i in range(200)]
    a = chunk(" ".join(words[:150]))
    b = chunk(" ".join(words[120:]))  # a typical chunk overlap

    assert deduplicate([a, b], 0.9) == ([a, b], 0)


def ranked(content: str, score: float | None) -> RankedChunk:
    return RankedChunk(chunk(content), score)


def test_context_selection_applies_threshold_top_k_and_budget() -> None:
    items = [ranked("a" * 400, 6.0), ranked("b" * 400, 3.0), ranked("c" * 400, -11.0), ranked("d" * 900, 2.0)]

    selection = select_context(items, top_k=5, min_rerank_score=-10, max_chars=1000)

    assert [r.chunk.content[0] for r in selection.passages] == ["a", "b"]
    assert (selection.below_threshold, selection.over_budget, selection.characters) == (1, 1, 800)

    capped = select_context(items, top_k=1, min_rerank_score=None, max_chars=10_000)
    assert len(capped.passages) == 1 and capped.beyond_top_k == 3


def test_context_selection_keeps_an_oversized_first_passage_and_unscored_passages() -> None:
    selection = select_context(
        [ranked("x" * 5000, None), ranked("y" * 10, None)], top_k=5, min_rerank_score=0, max_chars=1000
    )

    assert [r.chunk.content[0] for r in selection.passages] == ["x"]  # never empty because of the budget
    assert selection.below_threshold == 0  # no scores (reranker unavailable): the threshold can't apply


def test_citation_check_counts_verified_unverified_and_rejected() -> None:
    sources = [ranked(LEAVE, 5.0)]
    spans = [
        CitationSpan(0, "18 days of paid annual leave", 0, 10),
        CitationSpan(0, "a sentence the source does not contain", 0, 10),
        CitationSpan(3, "a source that was never sent", 0, 10),
    ]
    citations = map_citations(sources, spans)

    check = check_citations(spans, citations, len(sources))

    assert (check.cited_sources, check.quotes, check.verified_quotes, check.rejected) == (1, 2, 1, 1)
    assert not check.all_verified


# --- through the API ------------------------------------------------------------------------------


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


async def upload(client: AsyncClient, headers: dict, kb: str, name: str, text: str) -> str:
    response = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": (name, text.encode())},
        headers=headers,
    )
    document_id = response.json()["id"]
    await process_document(uuid.UUID(document_id))
    return document_id


@pytest.fixture
async def library(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    kb = (await client.post(f"{API}/knowledge-bases", json={"name": "Policies"}, headers=alice)).json()["id"]
    return {
        "kb": kb,
        "md": await upload(client, alice, kb, "leave.md", LEAVE),
        "txt": await upload(client, alice, kb, "leave_copy.txt", LEAVE + " "),  # same text, another file
        "remote": await upload(
            client, alice, kb, "remote.txt", "Remote work: three days per week with approval."
        ),
    }


async def search(client: AsyncClient, headers: dict, kb: str, **filters: object) -> dict:
    body: dict = {"query": "annual leave days", "knowledge_base_ids": [kb], "limit": 10}
    if filters:
        body["filters"] = filters
    response = await client.post(f"{API}/retrieval/search", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_duplicates_across_documents_are_removed_from_search(
    client: AsyncClient, alice: dict, library: dict
) -> None:
    body = await search(client, alice, library["kb"])

    filenames = [hit["filename"] for hit in body["results"]]
    assert body["duplicates_removed"] == 1
    assert sorted(f for f in filenames if f.startswith("leave")) in (["leave.md"], ["leave_copy.txt"])


async def test_metadata_filters(client: AsyncClient, alice: dict, library: dict, db: AsyncSession) -> None:
    by_type = await search(client, alice, library["kb"], file_types=[".txt"])
    by_document = await search(client, alice, library["kb"], document_ids=[library["md"]])
    await db.execute(
        update(Document)
        .where(Document.id == uuid.UUID(library["md"]))
        .values(created_at=datetime.now(UTC) - timedelta(days=30))
    )
    await db.commit()
    recent = await search(
        client, alice, library["kb"], uploaded_after=(datetime.now(UTC) - timedelta(days=1)).isoformat()
    )
    foreign = await search(client, alice, library["kb"], document_ids=[str(uuid.uuid4())])

    assert {h["filename"] for h in by_type["results"]} <= {"leave_copy.txt", "remote.txt"} and by_type[
        "filter_documents"
    ] == 2
    assert {h["filename"] for h in by_document["results"]} == {"leave.md"} and by_document[
        "duplicates_removed"
    ] == 0
    assert "leave.md" not in {h["filename"] for h in recent["results"]}
    assert foreign["results"] == [] and foreign["filter_documents"] == 0


async def test_unknown_file_type_filter_is_rejected(client: AsyncClient, alice: dict, library: dict) -> None:
    response = await client.post(
        f"{API}/retrieval/search",
        json={"query": "leave", "knowledge_base_ids": [library["kb"]], "filters": {"file_types": [".exe"]}},
        headers=alice,
    )

    assert response.status_code == 422


class KeywordReranker:
    """Scores 5 for passages mentioning 'leave', -12 otherwise (stands in for the cross-encoder)."""

    model_name = "keyword-reranker"

    async def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RankedChunk]:
        scored = [RankedChunk(c, 5.0 if "leave" in c.content.lower() else -12.0) for c in chunks]
        return sorted(scored, key=lambda r: -r.rerank_score)[:top_k]


async def test_answers_record_every_stage_and_validate_citations(
    client: AsyncClient, alice: dict, library: dict, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reranking, "get_reranker", KeywordReranker)
    monkeypatch.setattr(get_settings(), "rerank_min_score", -10.0)
    llm.answer = "Employees receive 18 days."
    llm.cite = [(0, "employees receive 18 days of paid annual leave each year")]

    response = await client.post(
        f"{API}/rag/answer",
        json={"question": "How many annual leave days?", "knowledge_base_ids": [library["kb"]]},
        headers=alice,
    )

    body = response.json()
    stats = body["retrieval"]
    assert body["answer_type"] == "knowledge_base"
    assert stats["duplicates_removed"] == 1
    assert stats["below_rerank_threshold"] >= 1  # the remote-work passage
    assert stats["context_passages"] == len(body["sources"]) == 1
    assert stats["context_chars"] == len(body["sources"][0]["content"])
    assert stats["citation_check"] == {"cited_sources": 1, "quotes": 1, "verified_quotes": 1, "rejected": 0}


async def test_nothing_above_the_threshold_means_not_found_without_the_model(
    client: AsyncClient, alice: dict, library: dict, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reranking, "get_reranker", KeywordReranker)
    monkeypatch.setattr(get_settings(), "rerank_min_score", 6.0)  # above every score

    body = (
        await client.post(
            f"{API}/rag/answer",
            json={"question": "How many leave days?", "knowledge_base_ids": [library["kb"]]},
            headers=alice,
        )
    ).json()

    assert body["answer_type"] == "not_found" and llm.calls == []
    assert body["retrieval"]["context_passages"] == 0 and body["retrieval"]["below_rerank_threshold"] >= 2


async def test_chat_filters_apply_to_the_turn_and_stats_are_saved(
    client: AsyncClient, alice: dict, library: dict, llm: ScriptedLLM
) -> None:
    llm.cite = [(0, "Remote work")]

    body = (
        await client.post(
            f"{API}/chat",
            json={
                "message": "How many remote work days per week?",
                "knowledge_base_id": library["kb"],
                "filters": {"document_ids": [library["remote"]]},
            },
            headers=alice,
        )
    ).json()

    assistant = body["assistant_message"]
    assert {s["filename"] for s in assistant["sources"]} == {"remote.txt"}
    assert assistant["retrieval"]["filter_documents"] == 1
    assert assistant["retrieval"]["citation_check"]["verified_quotes"] == 1
    stored = (await client.get(f"{API}/conversations/{body['conversation']['id']}", headers=alice)).json()
    assert stored["messages"][1]["retrieval"]["context_passages"] == 1
