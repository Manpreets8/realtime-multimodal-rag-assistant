"""Document insights: requesting, generating (single and multi-part), grounding, failures, jobs."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_client
from app.core.config import get_settings
from app.llm.base import LLMUnavailableError
from app.models import DocumentInsight, InsightStatus
from app.services import insights_service
from app.services.ingestion_service import process_document
from app.workers.ingestion_worker import IngestionWorker
from app.workers.job_queue import DEAD_LETTER_STREAM, GROUP, STREAM, JobQueue, processing_lock
from tests.conftest import RegisterFn, bearer
from tests.fakes import RecordingQueue, ScriptedLLM

pytestmark = pytest.mark.integration

API = "/api/v1"
TEXT = (
    "# Travel policy\n\nAcme Travel books all flights. Economy class is the default for flights "
    "under 6 hours. Business class is allowed for flights of 6 hours or longer with manager "
    "approval. Hotel costs are reimbursed up to 180 EUR per night in Berlin."
)


def analysis(**overrides: object) -> str:
    data = {
        "language": "English",
        "short_summary": "Rules for booking business travel.",
        "detailed_summary": "Economy is the default.\n\nBusiness class needs approval.",
        "technical_summary": "Flights under 6 hours: economy. Hotels: up to 180 EUR per night.",
        "key_points": ["Economy is the default", "Business class needs approval", "Economy is the default"],
        "topics": ["Business travel", "Expenses"],
        "keywords": ["business class", "manager approval", "quantum computing"],
        "entities": [
            {"name": "Acme Travel", "type": "organization"},
            {"name": "Berlin", "type": "location"},
            {"name": "Globex Corporation", "type": "organization"},
        ],
    }
    data.update(overrides)
    return json.dumps(data)


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


async def processed_document(
    client: AsyncClient, headers: dict, text: str = TEXT, name: str = "travel.md"
) -> str:
    kb = (await client.post(f"{API}/knowledge-bases", json={"name": f"KB {name}"}, headers=headers)).json()[
        "id"
    ]
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
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "llm_api_key", SecretStr("sk-ant-test"))


async def test_request_generate_and_read(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, ingestion_queue: RecordingQueue, configured: None
) -> None:
    document_id = await processed_document(client, alice)
    assert (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()[
        "status"
    ] == "none"

    requested = await client.post(f"{API}/documents/{document_id}/insights", headers=alice)

    assert requested.status_code == 202 and requested.json()["status"] == "pending"
    assert ingestion_queue.insights == [uuid.UUID(document_id)]

    llm.json_answers = [analysis()]
    assert await insights_service.generate(uuid.UUID(document_id)) is InsightStatus.READY

    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["status"] == "ready" and body["coverage"] == 1.0
    assert (body["model"], body["llm_calls"], body["input_tokens"], body["output_tokens"]) == (
        "test-llm",
        1,
        2000,
        300,
    )
    content = body["content"]
    assert content["short_summary"] == "Rules for booking business travel." and content["language"] == "English"
    assert content["key_points"] == [
        "Economy is the default",
        "Business class needs approval",
    ]  # deduplicated
    # Grounding: terms the document never mentions are removed.
    assert content["keywords"] == ["business class", "manager approval"]
    assert [e["name"] for e in content["entities"]] == ["Acme Travel", "Berlin"]
    assert content["ungrounded_removed"] == 2
    # The model was asked for schema-constrained JSON about this document's text only.
    [call] = llm.calls
    assert call["json_schema"] == insights_service.INSIGHT_SCHEMA and call["effort"] == "low"
    assert "Hotel costs are reimbursed up to 180 EUR" in call["messages"][0].text
    assert "Use only the document text" in call["system"]


async def test_long_documents_are_analysed_in_parts_then_merged(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "insights_chars_per_call", 5000)
    text = "\n\n".join(f"Section {i}. " + "Berlin hotel costs are reimbursed. " * 20 for i in range(18))
    document_id = await processed_document(client, alice, text, "long.md")
    await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    llm.json_answers = [analysis()] * 10

    await insights_service.generate(uuid.UUID(document_id))

    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    parts = [c for c in llm.calls if c["system"] == insights_service.ANALYSIS_SYSTEM_PROMPT]
    merges = [c for c in llm.calls if c["system"] == insights_service.MERGE_SYSTEM_PROMPT]
    assert len(parts) >= 2 and len(merges) == 1
    assert "Part 1 of" in parts[0]["messages"][0].text
    assert body["llm_calls"] == len(parts) + 1 and body["coverage"] == 1.0


async def test_very_long_documents_are_sampled_and_the_coverage_reported(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "insights_chars_per_call", 5000)
    monkeypatch.setattr(get_settings(), "insights_max_parts", 1)
    text = "\n\n".join(f"Section {i}. " + "Berlin hotel costs are reimbursed. " * 20 for i in range(18))
    document_id = await processed_document(client, alice, text, "huge.md")
    await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    llm.json_answers = [analysis()]

    await insights_service.generate(uuid.UUID(document_id))

    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["llm_calls"] == 1
    assert 0 < body["coverage"] < 1
    assert len(llm.calls[0]["messages"][0].text) <= 5000 + 200  # one part's text plus the header


def test_sampling_keeps_order_and_spreads_across_the_document() -> None:
    passages = [f"p{i:02d}-" + "x" * 96 for i in range(20)]  # 100 characters each

    selected, coverage = insights_service._select_passages(passages, 500)

    assert len(selected) == 5 and coverage == 0.25
    assert selected[0].startswith("p00") and selected[-1].startswith("p19")
    assert selected == sorted(selected)


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("not json at all", "unexpected format"),
        (json.dumps({"short_summary": "missing fields"}), "unexpected format"),
    ],
)
async def test_invalid_model_output_fails_with_a_clear_message(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, configured: None, answer: str, message: str
) -> None:
    document_id = await processed_document(client, alice)
    await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    llm.json_answers = [answer]

    assert await insights_service.generate(uuid.UUID(document_id)) is InsightStatus.FAILED

    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["status"] == "failed" and message in body["error_message"]
    assert body["llm_calls"] == 1 and body["input_tokens"] == 2000  # spent tokens are still recorded


async def test_an_unavailable_model_fails_with_its_message(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, configured: None
) -> None:
    document_id = await processed_document(client, alice)
    await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    llm.json_answers = [analysis()]
    llm.fail_with = LLMUnavailableError("The AI model is busy right now. Please try again shortly.")

    await insights_service.generate(uuid.UUID(document_id))

    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["status"] == "failed" and body["error_message"].startswith("The AI model is busy")


async def test_regenerating_keeps_the_previous_insights_visible_until_replaced(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, configured: None
) -> None:
    document_id = await processed_document(client, alice)
    await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    llm.json_answers = [analysis()]
    await insights_service.generate(uuid.UUID(document_id))

    again = await client.post(f"{API}/documents/{document_id}/insights", headers=alice)

    assert again.json()["status"] == "pending"
    assert again.json()["content"]["short_summary"] == "Rules for booking business travel."
    llm.json_answers = [analysis(short_summary="Updated rules for travel.")]
    assert await insights_service.generate(uuid.UUID(document_id)) is InsightStatus.READY
    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["status"] == "ready" and body["content"]["short_summary"] == "Updated rules for travel."


async def test_request_rules(
    client: AsyncClient,
    alice: dict,
    configured: None,
    ingestion_queue: RecordingQueue,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb = (await client.post(f"{API}/knowledge-bases", json={"name": "Q"}, headers=alice)).json()["id"]
    queued = (
        await client.post(
            f"{API}/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": ("q.md", b"Queued.")},
            headers=alice,
        )
    ).json()["id"]
    not_processed = await client.post(f"{API}/documents/{queued}/insights", headers=alice)
    assert not_processed.status_code == 409

    document_id = await processed_document(client, alice)
    first = await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    duplicate = await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    assert first.status_code == 202 and duplicate.status_code == 409

    # A request whose job was lost long ago can be made again.
    await db.execute(
        update(DocumentInsight)
        .where(DocumentInsight.document_id == uuid.UUID(document_id))
        .values(requested_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db.commit()
    assert (await client.post(f"{API}/documents/{document_id}/insights", headers=alice)).status_code == 202

    monkeypatch.setattr(get_settings(), "llm_api_key", None)
    unconfigured = await client.post(f"{API}/documents/{document_id}/insights", headers=alice)
    assert unconfigured.status_code == 503 and unconfigured.json()["error"]["code"] == "llm_not_configured"


async def test_queue_unavailable_marks_the_request_failed(
    client: AsyncClient, alice: dict, configured: None, ingestion_queue: RecordingQueue
) -> None:
    document_id = await processed_document(client, alice)
    ingestion_queue.available = False

    body = (await client.post(f"{API}/documents/{document_id}/insights", headers=alice)).json()

    assert body["status"] == "failed" and "unavailable" in body["error_message"]


async def test_generate_does_nothing_without_a_pending_request(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    document_id = await processed_document(client, alice)

    assert await insights_service.generate(uuid.UUID(document_id)) is None
    assert llm.calls == []


# --- jobs ---------------------------------------------------------------------------------------


async def test_automatic_mode_queues_insights_after_ingestion(
    client: AsyncClient, alice: dict, configured: None, redis_db: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "document_insights_auto", True)

    document_id = await processed_document(client, alice)

    jobs = await redis_db.xrange(STREAM)
    assert any(f.get(b"kind") == b"insights" and f[b"document_id"].decode() == document_id for _, f in jobs)
    body = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    assert body["status"] == "pending"


async def test_the_worker_routes_insights_jobs_with_their_own_lock(redis_db: Redis) -> None:
    document_id = uuid.uuid4()
    seen: list[tuple[str, bool]] = []

    async def ingest(doc: uuid.UUID) -> None:
        seen.append(("ingest", await redis_db.exists(processing_lock(doc)) == 1))

    async def insights(doc: uuid.UUID) -> None:
        seen.append(("insights", await redis_db.exists(processing_lock(doc, "insights")) == 1))

    await JobQueue(redis_client.get_redis()).enqueue_insights(document_id)
    worker = IngestionWorker(
        redis_client.get_redis(),
        ingest,
        insights_processor=insights,
        block_ms=50,
        run_maintenance=False,
        job_timeout_seconds=5,
    )
    await worker.ensure_group()
    task = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(5):
            while not seen:  # noqa: ASYNC110 - polling the worker's side effect
                await asyncio.sleep(0.02)
    finally:
        worker.stop()
        await asyncio.wait_for(task, 10)

    assert seen == [("insights", True)]


async def test_a_poison_insights_job_fails_the_insights_not_the_document(
    client: AsyncClient, alice: dict, configured: None, redis_db: Redis
) -> None:
    document_id = await processed_document(client, alice)
    await client.post(
        f"{API}/documents/{document_id}/insights", headers=alice
    )  # RecordingQueue: not in Redis
    await JobQueue(redis_client.get_redis()).enqueue_insights(uuid.UUID(document_id))
    worker = IngestionWorker(redis_client.get_redis(), block_ms=50, run_maintenance=False, max_deliveries=1)
    await worker.ensure_group()
    entry_id, fields = (await redis_db.xrange(STREAM))[0]
    # Simulate earlier deliveries that crashed their workers.
    for _ in range(2):
        await redis_db.xreadgroup(GROUP, "crashed", {STREAM: ">"}, count=1)
        await redis_db.xclaim(STREAM, GROUP, "crashed", 0, [entry_id])

    await worker._handle(entry_id.decode(), fields)

    insights = (await client.get(f"{API}/documents/{document_id}/insights", headers=alice)).json()
    document = (await client.get(f"{API}/documents/{document_id}", headers=alice)).json()
    assert insights["status"] == "failed" and "failed attempts" in insights["error_message"]
    assert document["status"] == "completed"
    [(_, dead)] = await redis_db.xrange(DEAD_LETTER_STREAM)
    assert dead[b"kind"] == b"insights"
