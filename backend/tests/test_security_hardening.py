"""Security properties checked across the whole API.

The authentication and authorization matrices are built from the OpenAPI schema, so a new
endpoint is covered automatically: it must reject anonymous callers, and one that takes a
resource ID must hide other users' resources, or these tests fail until it is added to
the lists below.
"""

import json
import logging
import re
import uuid

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.logging import REDACTED, JsonFormatter
from app.main import create_app
from app.models import DocumentChunk, User
from app.services.ingestion_service import process_document
from tests.conftest import DEFAULT_PASSWORD, RegisterFn, bearer
from tests.fakes import RecordingQueue, ScriptedLLM
from tests.images import PNG

pytestmark = pytest.mark.integration

API = "/api/v1"
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
}


def operations(app: FastAPI) -> list[tuple[str, str]]:
    return sorted((method.upper(), path) for path, ops in app.openapi()["paths"].items() for method in ops)


def fill(path: str, ids: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ids[m.group(1)], path)


# --- authentication: every non-public operation rejects anonymous and forged callers ------------


async def test_every_operation_requires_authentication(app: FastAPI, client: AsyncClient) -> None:
    random_ids = {
        name: str(uuid.uuid4())
        for name in ("kb_id", "document_id", "chunk_id", "conversation_id", "image_id")
    }
    forged = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.forged"}
    failures = []
    for method, path in operations(app):
        if (method, path) in PUBLIC:
            continue
        for headers in ({}, forged):
            response = await client.request(method, fill(path, random_ids), headers=headers)
            if response.status_code != 401 or response.json()["error"]["code"] != "unauthorized":
                failures.append((method, path, bool(headers), response.status_code))
    assert failures == []


def test_the_schema_declares_security_on_every_non_public_operation(app: FastAPI) -> None:
    spec = app.openapi()["paths"]
    unsecured = [
        (method, path)
        for method, path in operations(app)
        if (method, path) not in PUBLIC and not spec[path][method.lower()].get("security")
    ]
    assert unsecured == []


# --- authorization: other users' resources are invisible --------------------------------------------


@pytest.fixture
async def alice_resources(
    client: AsyncClient,
    register_user: RegisterFn,
    llm: ScriptedLLM,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """A knowledge base with an ingested document, a conversation and an image, all Alice's."""
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.0)
    alice = bearer((await register_user(email="alice@example.com"))["access_token"])
    kb_id = (await client.post(f"{API}/knowledge-bases", json={"name": "Private"}, headers=alice)).json()[
        "id"
    ]
    document = (
        await client.post(
            f"{API}/documents/upload",
            data={"knowledge_base_id": kb_id},
            files={"file": ("secret.txt", b"The launch code is 1234. Salaries are confidential.")},
            headers=alice,
        )
    ).json()
    await process_document(uuid.UUID(document["id"]))
    chunk_id = str(
        await db.scalar(
            select(DocumentChunk.id).where(DocumentChunk.document_id == uuid.UUID(document["id"]))
        )
    )
    image_id = (
        await client.post(f"{API}/images", files={"file": ("a.png", PNG, "image/png")}, headers=alice)
    ).json()["id"]
    conversation_id = (await client.post(f"{API}/chat", json={"message": "Hi"}, headers=alice)).json()[
        "conversation"
    ]["id"]
    return {
        "kb_id": kb_id,
        "document_id": document["id"],
        "chunk_id": chunk_id,
        "image_id": image_id,
        "conversation_id": conversation_id,
    }


# Minimal valid bodies, so a 404 comes from the ownership check rather than validation.
ID_ROUTE_BODIES: dict[tuple[str, str], dict] = {
    ("GET", "/api/v1/knowledge-bases/{kb_id}"): {},
    ("PATCH", "/api/v1/knowledge-bases/{kb_id}"): {"json": {"name": "Stolen"}},
    ("DELETE", "/api/v1/knowledge-bases/{kb_id}"): {},
    ("GET", "/api/v1/knowledge-bases/{kb_id}/documents"): {},
    ("GET", "/api/v1/documents/{document_id}"): {},
    ("DELETE", "/api/v1/documents/{document_id}"): {},
    ("POST", "/api/v1/documents/{document_id}/reprocess"): {},
    ("GET", "/api/v1/documents/{document_id}/chunks"): {},
    ("GET", "/api/v1/documents/{document_id}/download"): {},
    ("GET", "/api/v1/chunks/{chunk_id}"): {},
    ("GET", "/api/v1/conversations/{conversation_id}"): {},
    ("PATCH", "/api/v1/conversations/{conversation_id}"): {"json": {"title": "Stolen"}},
    ("DELETE", "/api/v1/conversations/{conversation_id}"): {},
    ("GET", "/api/v1/images/{image_id}/content"): {},
    ("DELETE", "/api/v1/images/{image_id}"): {},
}


def foreign_reference_requests(ids: dict[str, str]) -> list[tuple[str, str, dict]]:
    """Operations that take another resource's ID in the body or form instead of the path."""
    return [
        (
            "POST",
            "/api/v1/documents/upload",
            {"data": {"knowledge_base_id": ids["kb_id"]}, "files": {"file": ("x.txt", b"x")}},
        ),
        (
            "POST",
            "/api/v1/retrieval/search",
            {"json": {"query": "launch code", "knowledge_base_ids": [ids["kb_id"]]}},
        ),
        (
            "POST",
            "/api/v1/rag/answer",
            {"json": {"question": "launch code?", "knowledge_base_ids": [ids["kb_id"]]}},
        ),
        ("POST", "/api/v1/chat", {"json": {"message": "launch code?", "knowledge_base_id": ids["kb_id"]}}),
        ("POST", "/api/v1/chat", {"json": {"message": "hi", "conversation_id": ids["conversation_id"]}}),
        ("POST", "/api/v1/chat", {"json": {"message": "look", "image_ids": [ids["image_id"]]}}),
        (
            "POST",
            "/api/v1/multimodal/image",
            {"data": {"knowledge_base_id": ids["kb_id"]}, "files": {"file": ("a.png", PNG, "image/png")}},
        ),
    ]


def test_every_id_route_is_in_the_authorization_matrix(app: FastAPI) -> None:
    with_ids = {(m, p) for m, p in operations(app) if "{" in p}
    assert with_ids == set(ID_ROUTE_BODIES), "add new ID routes to ID_ROUTE_BODIES"


async def test_other_users_resources_are_not_found(
    client: AsyncClient,
    register_user: RegisterFn,
    alice_resources: dict[str, str],
    llm: ScriptedLLM,
    db: AsyncSession,
) -> None:
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])
    llm_calls_before = len(llm.calls)
    leaks = []
    for (method, path), body in ID_ROUTE_BODIES.items():
        response = await client.request(method, fill(path, alice_resources), headers=bob, **body)
        if response.status_code != 404:
            leaks.append((method, path, response.status_code))
    for method, path, body in foreign_reference_requests(alice_resources):
        response = await client.request(method, path, headers=bob, **body)
        if response.status_code != 404:
            leaks.append((method, path, json.dumps(body, default=str)[:60], response.status_code))

    assert leaks == []
    assert len(llm.calls) == llm_calls_before  # nothing of Alice's reached the model for Bob
    # Alice's data is untouched by Bob's attempts.
    alice = bearer(
        (
            await client.post(
                f"{API}/auth/login", json={"email": "alice@example.com", "password": DEFAULT_PASSWORD}
            )
        ).json()["access_token"]
    )
    kb = (await client.get(f"{API}/knowledge-bases/{alice_resources['kb_id']}", headers=alice)).json()
    conversation = (
        await client.get(f"{API}/conversations/{alice_resources['conversation_id']}", headers=alice)
    ).json()
    assert kb["name"] == "Private" and kb["document_count"] == 1
    assert conversation["title"] == "Hi"


async def test_a_404_does_not_reveal_whether_the_resource_exists(
    client: AsyncClient, register_user: RegisterFn, alice_resources: dict[str, str]
) -> None:
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])
    theirs = await client.get(f"{API}/documents/{alice_resources['document_id']}", headers=bob)
    missing = await client.get(f"{API}/documents/{uuid.uuid4()}", headers=bob)

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"]["message"] == missing.json()["error"]["message"]


# --- injection ---------------------------------------------------------------------------------------

PAYLOADS = [
    "'; DROP TABLE users; --",
    '" OR 1=1 --',
    "a & b | !c <-> d:* ''",  # tsquery operators
    "\\x00\\\\ %_ $1 ?",
    "<script>alert(1)</script>",
    "ﬁle naïve 🚀 日本語",
]


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_hostile_input_is_treated_as_data(
    client: AsyncClient, register_user: RegisterFn, db: AsyncSession, payload: str
) -> None:
    headers = bearer((await register_user())["access_token"])
    users_before = await db.scalar(select(func.count(User.id)))

    kb = await client.post(
        f"{API}/knowledge-bases", json={"name": payload[:100], "description": payload}, headers=headers
    )
    assert kb.status_code == 201, kb.text
    assert kb.json()["description"] == payload.strip()
    search = await client.post(
        f"{API}/retrieval/search",
        json={"query": payload, "knowledge_base_ids": [kb.json()["id"]]},
        headers=headers,
    )
    upload = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb.json()["id"]},
        files={"file": (f"{payload}.txt", b"content")},
        headers=headers,
    )

    assert search.status_code == 200, search.text
    assert upload.status_code == 201, upload.text
    assert "/" not in upload.json()["filename"] and "\\" not in upload.json()["filename"]
    assert await db.scalar(select(func.count(User.id))) == users_before
    assert (
        await db.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'users'"))
        == 1
    )


# --- headers and CORS ----------------------------------------------------------------------------------


async def test_security_headers_on_api_responses(client: AsyncClient) -> None:
    for response in (await client.get(f"{API}/health"), await client.get(f"{API}/auth/me")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "strict-transport-security" not in response.headers  # development: no HTTPS


async def test_api_docs_keep_working_without_the_api_csp(client: AsyncClient) -> None:
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers


async def test_hsts_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport

    from app.core.config import Environment

    monkeypatch.setattr(get_settings(), "environment", Environment.PRODUCTION)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"{API}/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


async def test_cors_allows_configured_origins_only_and_never_credentials(client: AsyncClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    allowed = await client.options(f"{API}/chat", headers={"Origin": "http://localhost:5173", **preflight})
    denied = await client.options(f"{API}/chat", headers={"Origin": "https://evil.example", **preflight})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in allowed.headers
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


# --- secrets never leave the server ----------------------------------------------------------------


async def test_secrets_never_appear_in_responses_or_logs(
    client: AsyncClient, register_user: RegisterFn, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    from app.llm.claude import LLMNotConfiguredError

    settings = get_settings()
    secrets = [settings.jwt_secret.get_secret_value(), DEFAULT_PASSWORD, "sk-ant-leak-canary"]
    caplog.set_level(logging.DEBUG)
    token = (await register_user())["access_token"]
    secrets.append(token)
    llm.fail_with = LLMNotConfiguredError("The AI model is not configured correctly (invalid API key).")

    responses = [
        await client.post(
            f"{API}/auth/login", json={"email": "user@example.com", "password": DEFAULT_PASSWORD + "x"}
        ),
        await client.post(
            f"{API}/auth/register", json={"email": "not-an-email", "password": DEFAULT_PASSWORD}
        ),
        await client.post(f"{API}/auth/register", json={"email": "a@b.co", "password": "sk-ant-leak-canary"}),
        await client.post(f"{API}/chat", json={"message": "hi"}, headers=bearer(token)),
        await client.get(f"{API}/documents/not-a-uuid", headers=bearer(token)),
    ]

    body = "\n".join(r.text for r in responses)
    logs = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert len(caplog.records) >= len(responses)  # logs were really captured, not a vacuous pass
    for secret in secrets:
        assert secret not in body
        assert secret not in logs
    assert "Traceback" not in body


def test_log_fields_named_like_credentials_are_redacted() -> None:
    record = logging.makeLogRecord(
        {
            "msg": "event",
            "api_key": "sk-1",
            "password": "p",
            "access_token": "t",
            "Authorization": "Bearer x",
            "input_tokens": 812,
            "document_id": "d1",
        }
    )

    payload = json.loads(JsonFormatter().format(record))

    assert (
        payload["api_key"]
        == payload["password"]
        == payload["access_token"]
        == payload["Authorization"]
        == REDACTED
    )
    assert payload["input_tokens"] == 812  # token *counts* are metrics, not credentials
    assert payload["document_id"] == "d1"


# --- infrastructure failures -----------------------------------------------------------------------


@pytest.fixture
def failing_app(ingestion_queue: RecordingQueue) -> FastAPI:
    """The real app plus test-only routes that fail the way infrastructure fails."""
    application = create_app()
    application.state.ingestion_queue = ingestion_queue

    @application.get("/_test/db-down")
    async def db_down() -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

    @application.get("/_test/connect-timeout")
    async def connect_timeout() -> None:
        raise TimeoutError()  # what asyncpg raises when PostgreSQL doesn't answer

    @application.get("/_test/slow-query")
    async def slow_query(db: DbSession) -> None:
        await db.execute(text("SET LOCAL statement_timeout = 50"))
        await db.execute(text("SELECT pg_sleep(1)"))

    @application.get("/_test/bug")
    async def bug(request: Request) -> None:
        raise RuntimeError("password=hunter2 in an internal error")

    return application


@pytest.mark.parametrize(
    ("path", "status", "code", "message"),
    [
        ("/_test/db-down", 503, "database_unavailable", "temporarily unavailable"),
        ("/_test/connect-timeout", 503, "service_unavailable", "temporarily unavailable"),
        ("/_test/slow-query", 504, "timeout", "took too long"),
        ("/_test/bug", 500, "internal_error", "unexpected error"),
    ],
    ids=["database-down", "connect-timeout", "statement-timeout", "bug"],
)
async def test_infrastructure_failures_get_clear_errors_without_details(
    failing_app: FastAPI, db: AsyncSession, path: str, status: int, code: str, message: str
) -> None:
    from httpx import ASGITransport

    transport = ASGITransport(app=failing_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert message in error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    for internal in ("SELECT", "refused", "pg_sleep", "hunter2", "Traceback"):
        assert internal not in response.text
    if status == 503:
        assert response.headers["retry-after"] == "5"


async def test_statement_timeout_is_set_on_every_connection(db: AsyncSession) -> None:
    assert await db.scalar(text("SHOW statement_timeout")) == "30s"


# --- request correlation: a failed RAG request can be traced end to end ---------------------------


async def test_a_failed_chat_request_can_be_traced_by_its_request_id(
    client: AsyncClient, register_user: RegisterFn, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    from app.llm.claude import LLMTimeoutError

    headers = bearer((await register_user())["access_token"])
    llm.fail_with = LLMTimeoutError("The AI model took too long to respond. Please try again.")
    caplog.set_level(logging.INFO)

    response = await client.post(f"{API}/chat", json={"message": "hi"}, headers=headers)

    request_id = response.json()["error"]["request_id"]
    assert response.status_code == 504 and request_id == response.headers["x-request-id"]
    lines = [json.loads(JsonFormatter().format(r)) for r in caplog.records]
    traced = [line for line in lines if line.get("request_id") == request_id]
    events = {line["message"] for line in traced}
    assert {"app_error", "request_completed"} <= events
    completed = next(line for line in traced if line["message"] == "request_completed")
    assert completed["status_code"] == 504 and completed["path"] == f"{API}/chat"
