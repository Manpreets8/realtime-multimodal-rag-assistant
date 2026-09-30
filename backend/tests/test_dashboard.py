"""Dashboard totals and activity, the all-documents list, and account settings."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, Message
from app.services.ingestion_service import process_document
from tests.conftest import DEFAULT_PASSWORD, RegisterFn, bearer
from tests.fakes import ScriptedLLM

API = "/api/v1"


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


async def new_kb(client: AsyncClient, headers: dict, name: str) -> str:
    return (await client.post(f"{API}/knowledge-bases", json={"name": name}, headers=headers)).json()["id"]


async def upload(
    client: AsyncClient, headers: dict, kb_id: str, name: str, text: bytes = b"Leave policy."
) -> str:
    response = await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb_id},
        files={"file": (name, text)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- dashboard ----------------------------------------------------------------------------------


async def test_dashboard_totals_ai_usage_and_activity(
    client: AsyncClient, alice: dict, register_user: RegisterFn, db: AsyncSession, llm: ScriptedLLM
) -> None:
    kb = await new_kb(client, alice, "Policies")
    ready = await upload(client, alice, kb, "leave.md")
    await process_document(uuid.UUID(ready))
    broken = await upload(client, alice, kb, "scan.txt", b"Old scan.")
    await db.execute(
        update(Document)
        .where(Document.id == uuid.UUID(broken))
        .values(status=DocumentStatus.FAILED, error_message="No text found.", processed_at=datetime.now(UTC))
    )
    await db.commit()
    await upload(client, alice, kb, "waiting.md", b"Queued.")
    chat = (await client.post(f"{API}/chat", json={"message": "Hello"}, headers=alice)).json()
    await client.post(
        f"{API}/chat", json={"message": "Again", "conversation_id": chat["conversation"]["id"]}, headers=alice
    )
    # An answer from 40 days ago is outside the 30-day window.
    await db.execute(
        update(Message)
        .where(Message.id == uuid.UUID(chat["assistant_message"]["id"]))
        .values(created_at=datetime.now(UTC) - timedelta(days=40))
    )
    await db.commit()
    # Another user's workspace never counts.
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])
    await new_kb(client, bob, "Bob's")
    await client.post(f"{API}/chat", json={"message": "Bob here"}, headers=bob)

    body = (await client.get(f"{API}/dashboard", headers=alice)).json()

    stats = body["stats"]
    assert stats["knowledge_bases"] == 1 and stats["conversations"] == 1
    assert stats["documents"] == {"total": 3, "indexed": 1, "processing": 1, "failed": 1}
    ai = stats["ai_answers"]
    assert (ai["days"], ai["answers"], ai["input_tokens"], ai["output_tokens"]) == (30, 1, 100, 20)
    assert len(ai["by_day"]) == 14 and ai["by_day"][-1] == {
        "date": datetime.now(UTC).date().isoformat(),
        "count": 1,
    }

    kinds = [item["kind"] for item in body["activity"]]
    assert {"knowledge_base_created", "document_uploaded", "document_ready", "document_failed"} <= set(kinds)
    assert "conversation_started" in kinds
    ready_item = next(item for item in body["activity"] if item["kind"] == "document_ready")
    assert ready_item["detail"] == "1 passage indexed"
    failed = next(item for item in body["activity"] if item["kind"] == "document_failed")
    assert (
        failed["title"] == "scan.txt"
        and failed["detail"] == "No text found."
        and failed["knowledge_base_id"] == kb
    )
    stamps = [item["at"] for item in body["activity"]]
    assert stamps == sorted(stamps, reverse=True)
    assert "Bob" not in str(body)


async def test_dashboard_for_a_new_user(client: AsyncClient, alice: dict) -> None:
    body = (await client.get(f"{API}/dashboard", headers=alice)).json()

    assert body["activity"] == []
    assert body["stats"]["documents"]["total"] == 0 and body["stats"]["ai_answers"]["answers"] == 0
    assert all(day["count"] == 0 for day in body["stats"]["ai_answers"]["by_day"])


# --- all documents ------------------------------------------------------------------------------


async def test_documents_across_knowledge_bases_with_filters(
    client: AsyncClient, alice: dict, register_user: RegisterFn
) -> None:
    hr, it = await new_kb(client, alice, "HR"), await new_kb(client, alice, "IT")
    ready = await upload(client, alice, hr, "leave_policy.md")
    await process_document(uuid.UUID(ready))
    await upload(client, alice, it, "vpn_100%.md", b"VPN setup.")
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])
    await upload(client, bob, await new_kb(client, bob, "Bob"), "bob_notes.md")

    everything = (await client.get(f"{API}/documents", headers=alice)).json()
    completed = (await client.get(f"{API}/documents", params={"status": "completed"}, headers=alice)).json()
    searched = (await client.get(f"{API}/documents", params={"search": "100%"}, headers=alice)).json()
    wildcard = (await client.get(f"{API}/documents", params={"search": "_"}, headers=alice)).json()
    page = (await client.get(f"{API}/documents", params={"limit": 1, "offset": 1}, headers=alice)).json()

    assert everything["total"] == 2
    assert {(d["filename"], d["knowledge_base_name"]) for d in everything["items"]} == {
        ("leave_policy.md", "HR"),
        ("vpn_100%.md", "IT"),
    }
    assert [d["filename"] for d in completed["items"]] == ["leave_policy.md"]
    assert [d["filename"] for d in searched["items"]] == ["vpn_100%.md"]
    assert wildcard["total"] == 2  # "_" is a literal underscore, and both names contain one
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (await client.get(f"{API}/documents", params={"status": "lost"}, headers=alice)).status_code == 422


# --- account settings ---------------------------------------------------------------------------


async def test_profile_name_can_be_changed_and_cleared(client: AsyncClient, alice: dict) -> None:
    named = await client.patch(f"{API}/auth/me", json={"full_name": "  Alice Liddell "}, headers=alice)
    cleared = await client.patch(f"{API}/auth/me", json={"full_name": "   "}, headers=alice)
    too_long = await client.patch(f"{API}/auth/me", json={"full_name": "x" * 121}, headers=alice)

    assert named.json()["full_name"] == "Alice Liddell"
    assert cleared.json()["full_name"] is None
    assert too_long.status_code == 422


async def test_changing_the_password_signs_out_every_other_session(
    client: AsyncClient, register_user: RegisterFn
) -> None:
    first = bearer((await register_user(email="carol@example.com"))["access_token"])
    login = await client.post(
        f"{API}/auth/login", json={"email": "carol@example.com", "password": DEFAULT_PASSWORD}
    )
    other_device = bearer(login.json()["access_token"])

    wrong = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": "not-it-1", "new_password": "brand-new-99"},
        headers=first,
    )
    same = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
        headers=first,
    )
    weak = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "onlyletters"},
        headers=first,
    )
    # A wrong current password is a 400, not a 401: the app must not sign the user out for a typo.
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "wrong_password")
    assert same.status_code == 400 and weak.status_code == 422
    assert (await client.get(f"{API}/auth/me", headers=first)).status_code == 200

    changed = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "brand-new-99"},
        headers=first,
    )

    assert changed.status_code == 200
    fresh = bearer(changed.json()["access_token"])
    assert (await client.get(f"{API}/auth/me", headers=fresh)).status_code == 200
    for stale in (first, other_device):
        assert (await client.get(f"{API}/auth/me", headers=stale)).status_code == 401
    old = await client.post(
        f"{API}/auth/login", json={"email": "carol@example.com", "password": DEFAULT_PASSWORD}
    )
    new = await client.post(
        f"{API}/auth/login", json={"email": "carol@example.com", "password": "brand-new-99"}
    )
    assert old.status_code == 401 and new.status_code == 200


async def test_sign_out_everywhere_else(client: AsyncClient, register_user: RegisterFn) -> None:
    first = bearer((await register_user(email="dan@example.com"))["access_token"])
    login = await client.post(
        f"{API}/auth/login", json={"email": "dan@example.com", "password": DEFAULT_PASSWORD}
    )
    other_device = bearer(login.json()["access_token"])

    response = await client.post(f"{API}/auth/logout-all", headers=first)

    assert response.status_code == 200
    assert (
        await client.get(f"{API}/auth/me", headers=bearer(response.json()["access_token"]))
    ).status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=other_device)).status_code == 401
    assert (await client.get(f"{API}/auth/me", headers=first)).status_code == 401
