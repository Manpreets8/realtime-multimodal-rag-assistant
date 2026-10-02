import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag import reranking
from app.rag.reranking import RankedChunk, relevance_label
from app.rag.retrieval import (
    SearchMode,
    normalize_query,
    reciprocal_rank_fusion,
    retrieve,
    topic_of,
    weighted_fusion,
)
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import HashingEmbeddingProvider

SEARCH = "/api/v1/retrieval/search"

HANDBOOK = {
    "leave.txt": b"Annual leave: employees receive 18 days of paid annual leave each year.",
    "remote.txt": b"Remote work: staff may work remotely three days per week with manager approval.",
    "errors.md": (
        b"# Troubleshooting\n\nError ERR-4521 means the VPN certificate expired. Renew it in the portal."
    ),
}


# --- pure functions ------------------------------------------------------------


def test_rrf_rewards_agreement_between_rankings() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    scores = reciprocal_rank_fusion([[a, b], [c, b]], k=60)

    assert scores[b] == pytest.approx(1 / 62 + 1 / 62)
    assert scores[a] == pytest.approx(1 / 61)
    assert max(scores, key=scores.get) == b  # in both lists beats first in one


def test_normalize_query() -> None:
    assert normalize_query("  what   is\n the\tpolicy? ") == "what is the policy?"
    assert normalize_query("   ") == ""
    assert len(normalize_query("x" * 5000)) == 2000


# --- API (real database, deterministic embeddings) ----------------------------


async def make_kb(client: AsyncClient, headers: dict, name: str, files: dict[str, bytes]) -> str:
    kb_id = (await client.post("/api/v1/knowledge-bases", json={"name": name}, headers=headers)).json()["id"]
    for filename, content in files.items():
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb_id},
            files={"file": (filename, content)},
            headers=headers,
        )
        await process_document(uuid.UUID(response.json()["id"]))
    return kb_id


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def bob(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="bob@example.com"))["access_token"])


@pytest.fixture
async def handbook(client: AsyncClient, alice: dict) -> str:
    return await make_kb(client, alice, "Handbook", HANDBOOK)


@pytest.fixture
def low_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    # The hashing test embedder yields lower similarities than a real model.
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)


async def search(client: AsyncClient, headers: dict, kb_ids: list[str], query: str, **extra) -> dict:
    response = await client.post(
        SEARCH, json={"query": query, "knowledge_base_ids": kb_ids, **extra}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


pytestmark = pytest.mark.integration


async def test_hybrid_search_ranks_the_relevant_chunk_first(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    body = await search(client, alice, [handbook], "How many days of annual leave do employees receive?")

    top = body["results"][0]
    assert top["filename"] == "leave.txt"
    assert "18 days" in top["content"]
    assert top["vector_rank"] == 1 and top["keyword_rank"] == 1
    assert top["similarity"] > 0.2 and top["keyword_score"] > 0
    assert body["mode"] == "hybrid"
    assert body["similarity_threshold"] == 0.2
    assert {"embedding", "vector_search", "keyword_search", "fusion", "fetch", "total"} <= set(
        body["timings_ms"]
    )


async def test_vector_and_keyword_modes_use_only_their_retriever(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    vector = await search(client, alice, [handbook], "remote work approval", mode="vector")
    keyword = await search(client, alice, [handbook], "remote work approval", mode="keyword")

    assert vector["results"][0]["filename"] == keyword["results"][0]["filename"] == "remote.txt"
    assert all(hit["keyword_rank"] is None for hit in vector["results"])
    assert all(hit["similarity"] is None and hit["vector_rank"] is None for hit in keyword["results"])
    assert "keyword_search" not in vector["timings_ms"]
    assert "embedding" not in keyword["timings_ms"]


async def test_keyword_search_matches_exact_codes_and_is_not_all_words(
    client: AsyncClient, alice: dict, handbook: str
) -> None:
    # Most of these words appear nowhere; an AND query would return nothing.
    body = await search(
        client, alice, [handbook], "what does ERR-4521 mean on my laptop today", mode="keyword"
    )

    assert [hit["filename"] for hit in body["results"]] == ["errors.md"]
    assert body["results"][0]["section"] == "Troubleshooting"


async def test_off_topic_queries_return_nothing(client: AsyncClient, alice: dict, handbook: str) -> None:
    body = await search(client, alice, [handbook], "sourdough baking temperature")

    assert body["results"] == []
    assert body["keyword_candidates"] == 0
    assert body["filtered_out"] == body["vector_candidates"] > 0


async def test_keyword_matches_survive_the_similarity_threshold(
    client: AsyncClient, alice: dict, handbook: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.99)

    body = await search(client, alice, [handbook], "ERR-4521")

    assert [hit["filename"] for hit in body["results"]] == ["errors.md"]
    assert body["results"][0]["similarity"] < 0.99


async def test_stopword_only_query_is_handled(client: AsyncClient, alice: dict, handbook: str) -> None:
    body = await search(client, alice, [handbook], "the and of", mode="keyword")

    assert body["results"] == []


async def test_limit_is_respected(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    body = await search(client, alice, [handbook], "leave remote error days", limit=2)

    assert len(body["results"]) == 2


async def test_search_spans_only_the_requested_knowledge_bases(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    cafeteria = await make_kb(
        client, alice, "Facilities", {"cafe.txt": b"The cafeteria serves paid lunch days."}
    )

    one = await search(client, alice, [handbook], "paid days", limit=10)
    both = await search(client, alice, [handbook, cafeteria], "paid days", limit=10)

    assert "cafe.txt" not in {hit["filename"] for hit in one["results"]}
    assert "cafe.txt" in {hit["filename"] for hit in both["results"]}


async def test_unprocessed_documents_are_not_searchable(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": handbook},
        files={"file": ("pending.txt", b"Parking permits are issued by facilities.")},
        headers=alice,
    )

    body = await search(client, alice, [handbook], "parking permits facilities")

    assert "pending.txt" not in {hit["filename"] for hit in body["results"]}


async def test_other_users_knowledge_bases_are_off_limits(
    client: AsyncClient, alice: dict, bob: dict, handbook: str, low_threshold: None
) -> None:
    bobs_kb = await make_kb(client, bob, "Bob", {"bob.txt": b"Employees receive 30 days of annual leave."})

    foreign = await client.post(
        SEARCH, json={"query": "annual leave", "knowledge_base_ids": [handbook]}, headers=bob
    )
    mixed = await client.post(
        SEARCH, json={"query": "annual leave", "knowledge_base_ids": [bobs_kb, handbook]}, headers=bob
    )
    own = await search(client, bob, [bobs_kb], "annual leave days", limit=10)

    assert foreign.status_code == 404
    assert mixed.status_code == 404
    assert {hit["filename"] for hit in own["results"]} == {"bob.txt"}


async def test_retrieve_filters_by_user_even_if_ownership_check_is_bypassed(
    client: AsyncClient, alice: dict, handbook: str, db: AsyncSession, embedder: HashingEmbeddingProvider
) -> None:
    result = await retrieve(
        db,
        embedder,
        user_id=uuid.uuid4(),  # not the owner
        knowledge_base_ids=[uuid.UUID(handbook)],
        query="annual leave",
        candidates=20,
        limit=5,
        similarity_threshold=0.0,
        mode=SearchMode.HYBRID,
    )

    assert result.chunks == []


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   ", "knowledge_base_ids": ["00000000-0000-0000-0000-000000000001"]},
        {"query": "ok", "knowledge_base_ids": [str(uuid.uuid4()) for _ in range(11)]},
        {"query": "ok", "knowledge_base_ids": [str(uuid.uuid4())], "limit": 0},
        {"query": "ok", "knowledge_base_ids": [str(uuid.uuid4())], "mode": "fuzzy"},
    ],
)
async def test_invalid_search_requests(client: AsyncClient, alice: dict, payload: dict) -> None:
    response = await client.post(SEARCH, json=payload, headers=alice)

    assert response.status_code == 422


async def test_search_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(SEARCH, json={"query": "x", "knowledge_base_ids": [str(uuid.uuid4())]})

    assert response.status_code == 401


@pytest.mark.model
async def test_default_threshold_separates_relevant_from_off_topic_with_the_real_model(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards SIMILARITY_THRESHOLD's measured default (0.5) against the default embedding model.

    Measured scores for these texts: relevant 0.57-0.74, off-topic 0.33-0.34. Note the known
    limitation this also documents: for one-sentence chunks bge-small rates "vacation days"
    about equally close to the travel text (it mentions "days") as to the leave text, so we
    only assert that the leave text passes the threshold, not that it ranks first. The
    reranker in the RAG pipeline is meant to resolve such near-ties."""
    from app.rag import embeddings
    from app.rag.embeddings import build_embedding_provider

    settings = get_settings()
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: build_embedding_provider(settings))
    monkeypatch.setattr(settings, "similarity_threshold", 0.5)
    leave = b"All full-time employees are entitled to 18 days of paid annual leave per calendar year."
    travel = b"Business meals are reimbursed up to 60 USD per day. Submit receipts within 30 days."
    kb = await make_kb(client, alice, "Handbook", {"leave.txt": leave, "travel.txt": travel})

    synonym = await search(client, alice, [kb], "How many vacation days do I get?", mode="vector")
    direct = await search(client, alice, [kb], "How much annual leave do I get?", mode="vector")
    off_topic = await search(client, alice, [kb], "What is the capital of France?", mode="vector")

    assert "leave.txt" in {hit["filename"] for hit in synonym["results"]}
    assert direct["results"][0]["filename"] == "leave.txt"
    assert direct["results"][0]["similarity"] >= 0.5
    assert off_topic["results"] == []
    assert off_topic["filtered_out"] == 2


# --- fusion methods and per-search options --------------------------------------------------------


def test_weighted_fusion_normalises_each_list_and_weights_them() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector = [(a, 0.9), (b, 0.6), (c, 0.3)]  # a best semantically
    keyword = [(c, 0.5), (b, 0.1)]  # c best by keywords; a not matched

    semantic_only = weighted_fusion(vector, keyword, 1.0)
    keyword_only = weighted_fusion(vector, keyword, 0.0)
    even = weighted_fusion(vector, keyword, 0.5)

    assert semantic_only == pytest.approx({a: 1.0, b: 0.5, c: 0.0})
    assert keyword_only == pytest.approx({a: 0.0, b: 0.0, c: 1.0})  # missing from a list = 0 for it
    assert even[c] == pytest.approx(0.5) and even[a] == pytest.approx(0.5) and even[b] == pytest.approx(0.25)
    single = weighted_fusion([(a, 0.42)], [], 0.5)
    assert single == {a: 0.5}  # one candidate normalises to 1.0, not a division by zero


async def test_search_options_override_the_server_settings(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    default = await search(client, alice, [handbook], "annual leave days")
    semantic_weighted = await search(
        client,
        alice,
        [handbook],
        "annual leave days",
        options={"fusion": "weighted", "alpha": 1.0, "candidates": 2},
    )

    assert (
        default["parameters"]["fusion"] == "rrf"
        and default["parameters"]["candidates"] == get_settings().top_k
    )
    params = semantic_weighted["parameters"]
    assert (params["fusion"], params["alpha"], params["candidates"]) == ("weighted", 1.0, 2)
    assert semantic_weighted["vector_candidates"] <= 2
    # alpha = 1 ranks purely by similarity (keyword-only matches, kept despite low similarity, come last).
    similarities = [hit["similarity"] for hit in semantic_weighted["results"]]
    assert similarities == sorted(similarities, reverse=True)


@pytest.mark.parametrize(
    "options",
    [
        {"alpha": 1.5},
        {"candidates": 0},
        {"candidates": 500},
        {"fusion": "max"},
        {"similarity_threshold": -0.1},
    ],
)
async def test_invalid_search_options(client: AsyncClient, alice: dict, handbook: str, options: dict) -> None:
    response = await client.post(
        SEARCH, json={"query": "leave", "knowledge_base_ids": [handbook], "options": options}, headers=alice
    )

    assert response.status_code == 422


# --- search across all knowledge bases; relevance labels ----------------------------------------


async def test_without_knowledge_bases_search_covers_all_of_mine_and_nobody_elses(
    client: AsyncClient, alice: dict, bob: dict, handbook: str, low_threshold: None
) -> None:
    it_kb = await make_kb(
        client, alice, "IT", {"vpn.txt": b"Annual VPN certificate renewal is in the portal."}
    )
    await make_kb(client, bob, "Bob's", {"bob.txt": b"Annual leave for Bob's team is 40 days."})

    body = await search(client, alice, [], "annual leave days VPN", limit=10)

    assert {hit["knowledge_base_id"] for hit in body["results"]} == {handbook, it_kb}
    assert all("Bob" not in hit["content"] for hit in body["results"])


async def test_a_user_without_knowledge_bases_gets_no_results(client: AsyncClient, bob: dict) -> None:
    body = await search(client, bob, [], "anything")

    assert body["results"] == []


def test_relevance_label_uses_measured_bands_only() -> None:
    model = "Xenova/ms-marco-MiniLM-L-6-v2"

    assert [relevance_label(model, s) for s in (9.6, 5.0, 4.9, -5.0, -5.1)] == [
        "high",
        "high",
        "medium",
        "medium",
        "low",
    ]
    assert relevance_label(model, None) is None
    assert relevance_label("rerank-2.5", 0.9) is None  # no measured bands: no label


RERANK_SCORES = {"leave.txt": 8.0, "remote.txt": 0.5, "errors.md": -9.0}


class CalibratedReranker:
    """Stands in for the local cross-encoder (same model name, so its bands apply)."""

    model_name = "Xenova/ms-marco-MiniLM-L-6-v2"

    async def rerank(self, query, chunks, top_k):
        ranked = [RankedChunk(c, RERANK_SCORES[c.filename]) for c in chunks]
        return sorted(ranked, key=lambda r: -r.rerank_score)[:top_k]


async def test_reranked_search_labels_relevance(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reranking, "get_reranker", CalibratedReranker)

    query = "annual leave remote work error"
    plain = await search(client, alice, [], query, limit=10)
    ranked = await search(client, alice, [], query, limit=10, options={"rerank": True})

    assert all(hit["relevance"] is None for hit in plain["results"])
    assert {hit["filename"]: hit["relevance"] for hit in ranked["results"]} == {
        "leave.txt": "high",
        "remote.txt": "medium",
        "errors.md": "low",
    }


@pytest.mark.parametrize(
    ("query", "topic"),
    [
        ("Find everything related to YOLO object detection", "YOLO object detection"),
        ("Show me all documents about travel booking", "travel booking"),
        ("Can you find anything regarding remote work?", "remote work"),
        ("list all notes on budgets", "budgets"),
        ("Everything about parental leave", "parental leave"),
        # Not phrased as requests: unchanged.
        ("What is the leave policy?", "What is the leave policy?"),
        ("for loops in python", "for loops in python"),
        ("Find the SEV2 response time", "Find the SEV2 response time"),
        ("Show me", "Show me"),
    ],
)
def test_topic_of_drops_request_phrasing_only(query: str, topic: str) -> None:
    assert topic_of(query) == topic


async def test_topic_option_searches_the_topic(
    client: AsyncClient, alice: dict, handbook: str, low_threshold: None
) -> None:
    request = "Find everything related to annual leave"

    as_typed = await search(client, alice, [], request)
    as_topic = await search(client, alice, [], request, options={"topic": True})

    assert (as_typed["query"], as_topic["query"]) == (request, "annual leave")
    assert as_topic["results"][0]["filename"] == "leave.txt"
