import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import RegisterFn, bearer
from tests.samples import PDF, TXT

pytestmark = pytest.mark.integration

KB = "/api/v1/knowledge-bases"


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def bob(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="bob@example.com"))["access_token"])


async def create_kb(
    client: AsyncClient, headers: dict[str, str], name: str = "Company Policies", **extra
) -> dict:
    response = await client.post(KB, json={"name": name, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_get_knowledge_base(client: AsyncClient, alice: dict) -> None:
    created = await create_kb(client, alice, "  Company   Policies ", description="  HR docs  ")

    assert created["name"] == "Company Policies"
    assert created["description"] == "HR docs"
    assert created["document_count"] == 0

    fetched = await client.get(f"{KB}/{created['id']}", headers=alice)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


async def test_list_returns_only_own_knowledge_bases_with_counts(
    client: AsyncClient, alice: dict, bob: dict
) -> None:
    kb = await create_kb(client, alice, "Alice KB")
    await create_kb(client, bob, "Bob KB")
    for name, content in [("a.pdf", PDF), ("b.txt", TXT)]:
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb["id"]},
            files={"file": (name, content)},
            headers=alice,
        )
        assert response.status_code == 201

    listing = (await client.get(KB, headers=alice)).json()

    assert [item["name"] for item in listing] == ["Alice KB"]
    assert listing[0]["document_count"] == 2
    detail = (await client.get(f"{KB}/{kb['id']}", headers=alice)).json()
    assert detail["status_counts"] == {"uploaded": 2}
    # The list reports the same breakdown as the detail endpoint (the dashboard relies on it).
    assert listing[0]["status_counts"] == detail["status_counts"]
    await create_kb(client, alice, "Empty KB")
    empty = next(item for item in (await client.get(KB, headers=alice)).json() if item["name"] == "Empty KB")
    assert (empty["document_count"], empty["status_counts"]) == (0, {})


async def test_names_are_unique_per_user_case_insensitively(
    client: AsyncClient, alice: dict, bob: dict
) -> None:
    await create_kb(client, alice, "Python Docs")

    duplicate = await client.post(KB, json={"name": "python docs"}, headers=alice)
    other_user = await client.post(KB, json={"name": "Python Docs"}, headers=bob)

    assert duplicate.status_code == 409
    assert other_user.status_code == 201


@pytest.mark.parametrize(
    "payload", [{"name": "   "}, {"name": "x" * 101}, {"name": "ok", "description": "d" * 1001}, {}]
)
async def test_create_validates_input(client: AsyncClient, alice: dict, payload: dict) -> None:
    response = await client.post(KB, json=payload, headers=alice)

    assert response.status_code == 422


async def test_update_knowledge_base(client: AsyncClient, alice: dict) -> None:
    kb = await create_kb(client, alice, "Old", description="keep me")

    renamed = await client.patch(f"{KB}/{kb['id']}", json={"name": "New"}, headers=alice)
    cleared = await client.patch(f"{KB}/{kb['id']}", json={"description": None}, headers=alice)

    assert renamed.json()["name"] == "New"
    assert renamed.json()["description"] == "keep me"
    assert cleared.json()["description"] is None


async def test_update_rejects_empty_body_and_name_collision(client: AsyncClient, alice: dict) -> None:
    await create_kb(client, alice, "Taken")
    kb = await create_kb(client, alice, "Mine")

    assert (await client.patch(f"{KB}/{kb['id']}", json={}, headers=alice)).status_code == 422
    assert (await client.patch(f"{KB}/{kb['id']}", json={"name": None}, headers=alice)).status_code == 422
    assert (await client.patch(f"{KB}/{kb['id']}", json={"name": "TAKEN"}, headers=alice)).status_code == 409


async def test_delete_removes_knowledge_base_documents_and_files(
    client: AsyncClient, alice: dict, upload_dir
) -> None:
    kb = await create_kb(client, alice)
    upload = await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("policy.pdf", PDF)},
        headers=alice,
    )
    document_id = upload.json()["id"]
    assert any(upload_dir.rglob("*.pdf"))

    response = await client.delete(f"{KB}/{kb['id']}", headers=alice)

    assert response.status_code == 204
    assert (await client.get(f"{KB}/{kb['id']}", headers=alice)).status_code == 404
    assert (await client.get(f"/api/v1/documents/{document_id}", headers=alice)).status_code == 404
    assert not any(path.is_file() for path in upload_dir.rglob("*"))


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "Hijacked"}),
        ("DELETE", "", None),
        ("GET", "/documents", None),
    ],
)
async def test_other_users_knowledge_base_is_not_found(
    client: AsyncClient, alice: dict, bob: dict, method: str, suffix: str, body: dict | None
) -> None:
    kb = await create_kb(client, alice)

    response = await client.request(method, f"{KB}/{kb['id']}{suffix}", json=body, headers=bob)

    assert response.status_code == 404
    still_there = await client.get(f"{KB}/{kb['id']}", headers=alice)
    assert still_there.json()["name"] == "Company Policies"


async def test_unknown_id_and_auth_required(client: AsyncClient, alice: dict) -> None:
    assert (await client.get(f"{KB}/{uuid.uuid4()}", headers=alice)).status_code == 404
    assert (await client.get(f"{KB}/not-a-uuid", headers=alice)).status_code == 422
    assert (await client.get(KB)).status_code == 401


# --- phase 4: statistics, search, sort, and the selected knowledge base scoping answers ---------


async def upload_text(client: AsyncClient, headers: dict, kb_id: str, name: str, text: bytes) -> str:
    response = await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": kb_id},
        files={"file": (name, text)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_statistics_and_last_activity(client: AsyncClient, alice: dict, db) -> None:
    from app.services.ingestion_service import process_document

    kb = await create_kb(client, alice, "Policies")
    first = b"Annual leave is 20 days."
    second = b"Remote work needs approval."
    for name, text in (("leave.md", first), ("remote.md", second)):
        await process_document(uuid.UUID(await upload_text(client, alice, kb["id"], name, text)))
    await client.post("/api/v1/chat", json={"message": "Hi", "knowledge_base_id": kb["id"]}, headers=alice)

    body = (await client.get(f"{KB}/{kb['id']}", headers=alice)).json()

    assert body["document_count"] == 2 and body["status_counts"] == {"completed": 2}
    assert body["passage_count"] == 2
    assert body["total_bytes"] == len(first) + len(second)
    assert body["conversation_count"] == 1
    # Uploading documents doesn't touch the knowledge base row, but it is activity.
    assert body["last_activity_at"] > body["updated_at"]
    listed = (await client.get(KB, headers=alice)).json()
    assert {k: listed[0][k] for k in ("passage_count", "total_bytes", "conversation_count")} == {
        "passage_count": 2,
        "total_bytes": len(first) + len(second),
        "conversation_count": 1,
    }


async def test_search_by_name_or_description(client: AsyncClient, alice: dict, bob: dict) -> None:
    await create_kb(client, alice, "Machine Learning", description="CNNs and transformers")
    await create_kb(client, alice, "Python")
    await create_kb(client, alice, "Budget 100% plan")
    await create_kb(client, bob, "Machine Learning notes")

    def names(response) -> list[str]:
        return sorted(kb["name"] for kb in response.json())

    assert names(await client.get(KB, params={"search": "machine"}, headers=alice)) == ["Machine Learning"]
    assert names(await client.get(KB, params={"search": "TRANSFORM"}, headers=alice)) == ["Machine Learning"]
    assert names(await client.get(KB, params={"search": "100%"}, headers=alice)) == ["Budget 100% plan"]
    assert names(await client.get(KB, params={"search": "%"}, headers=alice)) == ["Budget 100% plan"]
    assert (await client.get(KB, params={"search": "zzz"}, headers=alice)).json() == []


async def test_sort_orders(client: AsyncClient, alice: dict) -> None:
    beta = await create_kb(client, alice, "beta")
    await create_kb(client, alice, "Alpha")
    await create_kb(client, alice, "gamma")
    # A document upload makes the oldest knowledge base the most recently active.
    await upload_text(client, alice, beta["id"], "note.md", b"Fresh content.")

    def order(sort: str):
        return client.get(KB, params={"sort": sort}, headers=alice)

    assert [kb["name"] for kb in (await order("name")).json()] == ["Alpha", "beta", "gamma"]
    assert [kb["name"] for kb in (await order("created")).json()] == ["gamma", "Alpha", "beta"]
    assert (await order("recent")).json()[0]["name"] == "beta"
    assert (await order("size")).status_code == 422


async def test_the_selected_knowledge_base_is_the_only_one_the_answer_can_use(
    client: AsyncClient, alice: dict, llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.services.ingestion_service import process_document

    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    ml = await create_kb(client, alice, "Machine Learning")
    travel = await create_kb(client, alice, "Travel")
    await process_document(
        uuid.UUID(
            await upload_text(
                client, alice, travel["id"], "travel.md", b"Hotel costs are reimbursed up to 180 EUR."
            )
        )
    )
    question = "How much are hotel costs reimbursed?"

    wrong_kb = await client.post(
        "/api/v1/chat", json={"message": question, "knowledge_base_id": ml["id"]}, headers=alice
    )
    assert wrong_kb.json()["assistant_message"]["answer_type"] == "not_found"
    assert llm.calls == []  # nothing from the other knowledge base was retrieved, so the model was not asked

    llm.cite = [(0, "Hotel costs are reimbursed up to 180 EUR.")]
    right_kb = await client.post(
        "/api/v1/chat", json={"message": question, "knowledge_base_id": travel["id"]}, headers=alice
    )
    assert right_kb.json()["assistant_message"]["answer_type"] == "knowledge_base"
    assert {s["filename"] for s in right_kb.json()["assistant_message"]["sources"]} == {"travel.md"}


async def test_deleting_a_knowledge_base_keeps_its_conversations_as_general_chats(
    client: AsyncClient, alice: dict
) -> None:
    kb = await create_kb(client, alice, "Temporary")
    chat = (
        await client.post(
            "/api/v1/chat", json={"message": "Hi", "knowledge_base_id": kb["id"]}, headers=alice
        )
    ).json()

    await client.delete(f"{KB}/{kb['id']}", headers=alice)

    conversation = (
        await client.get(f"/api/v1/conversations/{chat['conversation']['id']}", headers=alice)
    ).json()
    assert conversation["knowledge_base_id"] is None
    assert conversation["messages"][0]["content"] == "Hi"  # history is kept
