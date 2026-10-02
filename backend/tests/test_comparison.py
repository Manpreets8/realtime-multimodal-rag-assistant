import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.base import LLMTimeoutError
from app.models import Document, DocumentStatus
from app.services.comparison_service import COMPARE_SYSTEM_PROMPT, diff, select_passages, sentences
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM

API = "/api/v1"
COMPARE = f"{API}/documents/compare"

RESUME_V1 = """Jane Doe

Senior Data Analyst at Acme since 2021.
Skills: Python, SQL, Tableau.
Led a team of 3 analysts.
Based in London."""

RESUME_V2 = """Jane Doe

Senior Data Analyst at Acme since 2021.
Skills: Python, SQL, Tableau, dbt.
Led a team of 5 analysts.
Certified AWS Machine Learning Specialist.
Based in London."""


def chunk(content: str, index: int = 0, page: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), content=content, chunk_index=index, page_number=page, section=None
    )


# --- text differences (no AI) ---------------------------------------------------------------------


def test_sentences_are_split_normalised_and_deduplicated_across_overlapping_chunks() -> None:
    first = chunk("Intro line\nThe fee is $20. Payment is due monthly.", 0)
    second = chunk("Payment is due monthly. Late payment costs 5%.", 1)  # overlap repeats a sentence

    units = sentences([first, second])

    assert [u.text for u in units] == [
        "Intro line",
        "The fee is $20.",
        "Payment is due monthly.",
        "Late payment costs 5%.",
    ]
    assert units[3].chunk is second


def test_diff_finds_added_removed_modified_and_common() -> None:
    a = sentences([chunk(RESUME_V1)])
    b = sentences([chunk(RESUME_V2)])

    result = diff(a, b)

    assert [(x.text, y.text) for x, y, _ in result.modified] == [
        ("Skills: Python, SQL, Tableau.", "Skills: Python, SQL, Tableau, dbt."),
        ("Led a team of 3 analysts.", "Led a team of 5 analysts."),
    ]
    assert [u.text for u in result.added] == ["Certified AWS Machine Learning Specialist."]
    assert result.removed == []
    assert [u.text for u in result.common] == [
        "Jane Doe",
        "Senior Data Analyst at Acme since 2021.",
        "Based in London.",
    ]
    assert result.overlap == pytest.approx(2 * 3 / (5 + 6), abs=1e-4)  # 5 and 6 distinct sentences


def test_unrelated_sentences_are_not_paired_as_modified() -> None:
    result = diff(
        sentences([chunk("Cats sleep a lot.")]), sentences([chunk("Quarterly revenue rose by 12%.")])
    )

    assert result.modified == []
    assert len(result.added) == len(result.removed) == 1


def test_select_passages_keeps_the_differing_chunks_first_within_the_budget() -> None:
    chunks = [chunk("x" * 1000, i) for i in range(5)]

    everything, coverage = select_passages(chunks, set(), budget=10_000)
    assert len(everything) == 5 and coverage == 1.0

    chosen, coverage = select_passages(chunks, {chunks[3].id, chunks[4].id}, budget=2_500)
    assert [c.chunk_index for c in chosen] == [3, 4]  # the differences, in document order
    assert coverage == pytest.approx(0.4)


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def bob(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="bob@example.com"))["access_token"])


async def upload(
    client: AsyncClient, headers: dict, kb: str, name: str, text: str, *, process: bool = True
) -> str:
    response = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": (name, text.encode())},
        headers=headers,
    )
    document_id = response.json()["id"]
    if process:
        await process_document(uuid.UUID(document_id))
    return document_id


@pytest.fixture
async def resumes(client: AsyncClient, alice: dict) -> dict[str, str]:
    kb = (await client.post(f"{API}/knowledge-bases", json={"name": "Career"}, headers=alice)).json()["id"]
    return {
        "kb": kb,
        "v1": await upload(client, alice, kb, "Resume_v1.txt", RESUME_V1),
        "v2": await upload(client, alice, kb, "Resume_v2.txt", RESUME_V2),
    }


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_api_key", SecretStr("test-key"))


async def test_compare_returns_text_differences_and_an_analysis_citing_both_documents(
    client: AsyncClient, alice: dict, resumes: dict, llm: ScriptedLLM, configured: None
) -> None:
    llm.answer = "## Modified information\n- The team grew from 3 to 5 analysts."
    llm.cite = [(0, "Led a team of 3 analysts."), (1, "Led a team of 5 analysts.")]

    response = await client.post(
        COMPARE, json={"document_a_id": resumes["v1"], "document_b_id": resumes["v2"]}, headers=alice
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_a"]["filename"] == "Resume_v1.txt" and body["document_b"]["coverage"] == 1.0
    assert body["differences"]["counts"] == {"added": 1, "removed": 0, "modified": 2, "common": 3}
    assert body["differences"]["added"][0]["text"] == "Certified AWS Machine Learning Specialist."
    assert body["differences"]["modified"][1]["before"]["text"] == "Led a team of 3 analysts."

    analysis = body["analysis"]
    assert set(analysis["cited_documents"]) == {resumes["v1"], resumes["v2"]}
    assert [c["filename"] for c in analysis["citations"]] == ["Resume_v1.txt", "Resume_v2.txt"]
    assert analysis["citation_check"]["verified_quotes"] == 2
    assert [s["filename"] for s in analysis["sources"]] == ["Resume_v1.txt", "Resume_v2.txt"]

    call = llm.answer_calls[0]
    assert call["system"] == COMPARE_SYSTEM_PROMPT
    parts = call["messages"][0].parts
    assert [p.title for p in parts[:2]] == ["Document A: Resume_v1.txt", "Document B: Resume_v2.txt"]
    assert "Compare Document A (Resume_v1.txt) with Document B (Resume_v2.txt)." in parts[-1].text


async def test_without_a_configured_model_the_text_differences_are_still_returned(
    client: AsyncClient, alice: dict, resumes: dict, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "llm_api_key", None)

    body = (
        await client.post(
            COMPARE, json={"document_a_id": resumes["v1"], "document_b_id": resumes["v2"]}, headers=alice
        )
    ).json()

    assert body["analysis"] is None and "not configured" in body["analysis_unavailable"]
    assert body["differences"]["counts"]["added"] == 1
    assert llm.calls == []


async def test_a_failed_ai_call_keeps_the_text_differences(
    client: AsyncClient, alice: dict, resumes: dict, llm: ScriptedLLM, configured: None
) -> None:
    llm.fail_with = LLMTimeoutError("The AI model took too long to respond.")

    body = (
        await client.post(
            COMPARE, json={"document_a_id": resumes["v1"], "document_b_id": resumes["v2"]}, headers=alice
        )
    ).json()

    assert body["analysis"] is None
    assert body["analysis_unavailable"].startswith("The AI comparison failed: The AI model took too long")
    assert body["differences"]["counts"]["modified"] == 2


async def test_long_documents_are_cut_with_coverage_reported(
    client: AsyncClient,
    alice: dict,
    resumes: dict,
    llm: ScriptedLLM,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_text = "\n\n".join(f"Paragraph {i}: " + "words " * 150 for i in range(12))
    a = await upload(client, alice, resumes["kb"], "long_a.txt", long_text)
    b = await upload(
        client, alice, resumes["kb"], "long_b.txt", long_text.replace("Paragraph 11:", "Final paragraph:")
    )
    monkeypatch.setattr(get_settings(), "compare_max_chars_per_document", 2_000)

    body = (await client.post(COMPARE, json={"document_a_id": a, "document_b_id": b}, headers=alice)).json()

    assert 0 < body["document_a"]["coverage"] < 1
    instruction = llm.answer_calls[0]["messages"][0].parts[-1].text
    assert "only" in instruction and "passages were provided" in instruction
    # The changed paragraph is among the passages sent, even though it is the last one.
    titles_and_text = [p.text for p in llm.answer_calls[0]["messages"][0].parts[:-1]]
    assert any("Final paragraph:" in text for text in titles_and_text)
    assert any("Paragraph 11:" in text for text in titles_and_text)


async def test_compare_rejects_other_users_unready_and_identical_documents(
    client: AsyncClient, alice: dict, bob: dict, resumes: dict, db: AsyncSession
) -> None:
    pair = {"document_a_id": resumes["v1"], "document_b_id": resumes["v2"]}

    assert (await client.post(COMPARE, json=pair, headers=bob)).status_code == 404
    same = await client.post(COMPARE, json={**pair, "document_b_id": resumes["v1"]}, headers=alice)
    assert same.status_code == 422
    await db.execute(
        update(Document)
        .where(Document.id == uuid.UUID(resumes["v2"]))
        .values(status=DocumentStatus.PROCESSING)
    )
    await db.commit()
    busy = await client.post(COMPARE, json=pair, headers=alice)
    assert busy.status_code == 409 and "still being processed" in busy.json()["error"]["message"]
    assert (await client.post(COMPARE, json=pair)).status_code == 401
