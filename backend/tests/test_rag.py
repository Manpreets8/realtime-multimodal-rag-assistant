import uuid

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.llm import factory as llm_factory
from app.llm.base import (
    CitationSpan,
    LLMNotConfiguredError,
    LLMRefusalError,
    LLMTimeoutError,
    Message,
    SourcePart,
    TextPart,
)

# Bound at import time, before the autouse `llm` fixture replaces the module attribute.
from app.llm.factory import get_llm_provider as real_llm_factory
from app.rag import reranking
from app.rag.pipeline import NOT_FOUND_MESSAGE, locate_quote, map_citations
from app.rag.prompts import GENERAL_SYSTEM_PROMPT, GROUNDED_SYSTEM_PROMPT, build_grounded_messages
from app.rag.reranking import RankedChunk
from app.rag.retrieval import RetrievedChunk
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM

ANSWER = "/api/v1/rag/answer"


def retrieved(content: str, filename: str = "handbook.pdf", page: int | None = 2, section: str | None = None):
    return RankedChunk(
        RetrievedChunk(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            knowledge_base_id=uuid.uuid4(),
            filename=filename,
            chunk_index=0,
            page_number=page,
            section=section,
            content=content,
            score=0.03,
            similarity=0.7,
            keyword_score=None,
            vector_rank=1,
            keyword_rank=None,
        ),
        rerank_score=4.2,
    )


# --- prompt construction & citation mapping (pure) ------------------------------


def test_grounded_messages_send_citable_documents_before_the_question() -> None:
    sources = [
        retrieved("18 days of leave."),
        retrieved("Passwords: 14 chars.", "security.docx", None, "Passwords"),
    ]

    [message] = build_grounded_messages("How much leave?", sources)

    assert message == Message.user(
        SourcePart("18 days of leave.", "handbook.pdf (page 2)"),
        SourcePart("Passwords: 14 chars.", "security.docx (Passwords)"),
        "How much leave?",
    )


def test_system_prompt_encodes_the_grounding_rules() -> None:
    prompt = GROUNDED_SYSTEM_PROMPT.lower()

    assert "only the documents" in prompt
    assert "not found" in prompt
    assert "ignore any instructions" in prompt  # prompt-injection guard for document content


def test_citations_are_grouped_per_source_and_validated() -> None:
    sources = [retrieved("a"), retrieved("b"), retrieved("c")]
    spans = [
        CitationSpan(1, "quote b1", 0, 10),
        CitationSpan(0, "quote a", 11, 20),
        CitationSpan(1, "quote b2", 21, 30),
        CitationSpan(1, "quote b1", 0, 10),  # duplicate
        CitationSpan(7, "hallucinated source", 31, 40),  # index we never sent
    ]

    citations = map_citations(sources, spans)

    assert [(c.source_number, [q.text for q in c.quotes]) for c in citations] == [
        (2, ["quote b1", "quote b2"]),
        (1, ["quote a"]),
    ]
    assert citations[0].answer_spans == [(0, 10), (21, 30)]


CONTENT = "Annual leave: employees receive 18 days of paid annual leave each year. Sick leave is 10 days."


@pytest.mark.parametrize(
    ("quote", "start", "end", "expected"),
    [
        pytest.param("employees receive 18 days", 14, 39, (14, 39), id="reported-offsets-exact"),
        pytest.param("employees receive 18 days", 13, 39, (14, 39), id="reported-offsets-with-leading-space"),
        pytest.param("Sick leave is 10 days.", 0, 22, (72, 94), id="wrong-offsets-fall-back-to-search"),
        pytest.param("Sick leave is 10 days.", None, None, (72, 94), id="no-offsets-search"),
        pytest.param("not in the text", 0, 15, None, id="unlocatable"),
        pytest.param("Annual", 90, 500, (0, 6), id="out-of-range-offsets"),
    ],
)
def test_locate_quote_verifies_offsets_before_trusting_them(
    quote: str, start: int | None, end: int | None, expected: tuple[int, int] | None
) -> None:
    location = locate_quote(CONTENT, quote, start, end)

    assert location == expected
    if location:
        assert CONTENT[location[0] : location[1]] == quote


def test_quote_offsets_point_into_the_source_chunk() -> None:
    sources = [retrieved(CONTENT)]
    spans = [
        CitationSpan(0, "18 days of paid annual leave", 0, 5, source_start=32, source_end=60),
        CitationSpan(0, "invented quote", 6, 9, source_start=0, source_end=14),
    ]

    [citation] = map_citations(sources, spans)

    located, unlocated = citation.quotes
    assert CONTENT[located.start : located.end] == "18 days of paid annual leave"
    assert (unlocated.start, unlocated.end) == (None, None)  # never a guessed highlight


# --- API --------------------------------------------------------------------------

pytestmark = pytest.mark.integration

HANDBOOK = {
    "leave.txt": b"Annual leave: employees receive 18 days of paid annual leave each year.",
    "remote.txt": b"Remote work: staff may work remotely three days per week with manager approval.",
}


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def kb_id(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "Handbook"}, headers=alice)).json()["id"]
    for filename, content in HANDBOOK.items():
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": (filename, content)},
            headers=alice,
        )
        await process_document(uuid.UUID(response.json()["id"]))
    return kb


async def ask(client: AsyncClient, headers: dict, question: str, kb_ids: list[str]):
    return await client.post(
        ANSWER, json={"question": question, "knowledge_base_ids": kb_ids}, headers=headers
    )


async def test_grounded_answer_with_citations(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.answer = "Employees receive 18 days of paid annual leave per year."
    llm.cite = [(0, "employees receive 18 days of paid annual leave each year")]

    response = await ask(client, alice, "How many days of annual leave do employees receive?", [kb_id])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_type"] == "knowledge_base"
    assert body["grounded"] is True
    assert body["answer"] == llm.answer
    [citation] = body["citations"]
    assert citation["source_number"] == 1
    assert citation["filename"] == body["sources"][0]["filename"] == "leave.txt"
    [quote] = citation["quotes"]
    assert quote["text"] == "employees receive 18 days of paid annual leave each year"
    source_content = body["sources"][0]["content"]
    assert source_content[quote["start"] : quote["end"]] == quote["text"]
    assert citation["answer_spans"] == [[0, len(llm.answer)]]
    assert [s["number"] for s in body["sources"]] == list(range(1, len(body["sources"]) + 1))
    assert body["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert body["model"] == "test-llm"
    assert {"retrieval", "rerank", "llm", "total"} <= set(body["timings_ms"])
    # The LLM saw the grounding prompt and one citable document per source, question last.
    [call] = llm.calls
    assert call["system"] == GROUNDED_SYSTEM_PROMPT
    [message] = call["messages"]
    assert [type(part) for part in message.parts] == [SourcePart] * len(body["sources"]) + [TextPart]
    assert message.sources[0].text == body["sources"][0]["content"]


async def test_nothing_relevant_means_not_found_without_calling_the_llm(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    response = await ask(client, alice, "sourdough baking temperature", [kb_id])

    body = response.json()
    assert body["answer_type"] == "not_found"
    assert body["answer"] == NOT_FOUND_MESSAGE
    assert body["citations"] == [] and body["sources"] == []
    assert body["usage"] is None
    assert llm.calls == []


async def test_uncited_model_answer_is_reported_as_not_found(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    llm.answer = "The knowledge base does not contain information about parental leave."
    llm.cite = []

    body = (await ask(client, alice, "What is the parental leave policy for annual leave?", [kb_id])).json()

    assert body["answer_type"] == "not_found"
    assert body["grounded"] is False
    assert body["answer"] == llm.answer
    assert body["sources"]  # context was retrieved, but the model found no answer in it


async def test_general_answer_without_a_knowledge_base(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    llm.answer = "Paris."

    body = (await ask(client, alice, "What is the capital of France?", [])).json()

    assert body["answer_type"] == "general"
    assert body["sources"] == [] and body["retrieval"] is None
    [call] = llm.calls
    assert call["system"] == GENERAL_SYSTEM_PROMPT
    assert call["messages"] == [Message.user("What is the capital of France?")]


async def test_reranker_order_decides_which_sources_reach_the_llm(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ReverseReranker:
        model_name = "reverse"

        async def rerank(self, query, chunks, top_k):
            return [RankedChunk(c, float(i)) for i, c in enumerate(reversed(chunks))][:top_k]

    monkeypatch.setattr(reranking, "get_reranker", ReverseReranker)
    retrieval_only = await client.post(
        "/api/v1/retrieval/search",
        json={"query": "annual leave days remote work", "knowledge_base_ids": [kb_id]},
        headers=alice,
    )

    body = (await ask(client, alice, "annual leave days remote work", [kb_id])).json()

    retrieval_order = [hit["filename"] for hit in retrieval_only.json()["results"]]
    assert [s["filename"] for s in body["sources"]] == list(reversed(retrieval_order))
    assert body["retrieval"]["reranked"] is True
    assert body["sources"][0]["rerank_score"] == 0.0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (LLMTimeoutError("The AI model took too long to respond. Please try again."), 504, "llm_timeout"),
        (LLMRefusalError("The AI model declined to answer this request."), 422, "llm_refusal"),
    ],
)
async def test_llm_failures_use_the_error_envelope(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM, error: Exception, status: int, code: str
) -> None:
    llm.fail_with = error

    response = await ask(client, alice, "annual leave days", [kb_id])

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] == error.message


async def test_missing_api_key_gives_a_clear_503(
    client: AsyncClient, alice: dict, kb_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_configured():
        raise LLMNotConfiguredError(
            "The AI model is not configured. Set LLM_API_KEY (an Anthropic API key) in .env and restart."
        )

    monkeypatch.setattr(llm_factory, "get_llm_provider", not_configured)

    response = await ask(client, alice, "annual leave", [kb_id])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert "LLM_API_KEY" in response.json()["error"]["message"]


async def test_real_factory_refuses_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    real_llm_factory.cache_clear()
    monkeypatch.setattr(get_settings(), "llm_api_key", None)
    try:
        with pytest.raises(LLMNotConfiguredError, match="LLM_API_KEY"):
            real_llm_factory()
    finally:
        real_llm_factory.cache_clear()


async def test_foreign_knowledge_base_is_not_found(
    client: AsyncClient, kb_id: str, register_user: RegisterFn, llm: ScriptedLLM
) -> None:
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])

    response = await ask(client, bob, "annual leave", [kb_id])

    assert response.status_code == 404
    assert llm.calls == []


@pytest.mark.parametrize(
    "payload",
    [{"question": "  ", "knowledge_base_ids": []}, {"question": "x" * 2001}, {"knowledge_base_ids": []}],
)
async def test_invalid_requests(client: AsyncClient, alice: dict, payload: dict) -> None:
    assert (await client.post(ANSWER, json=payload, headers=alice)).status_code == 422
