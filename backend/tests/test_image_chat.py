import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Message
from app.rag.prompts import DEFAULT_IMAGE_QUESTION, IMAGE_SYSTEM_PROMPT, MULTIMODAL_SYSTEM_PROMPT
from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedLLM
from tests.images import JPEG, PNG, image_bytes

pytestmark = pytest.mark.integration

IMAGES = "/api/v1/images"
CHAT = "/api/v1/chat"


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def kb_id(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "similarity_threshold", 0.2)  # hashing embedder scale
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "IT"}, headers=alice)).json()["id"]
    response = await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": kb},
        files={
            "file": (
                "vpn.md",
                b"# VPN\n\nError ERR-4521 means the VPN certificate expired. Renew it in the portal.",
            )
        },
        headers=alice,
    )
    await process_document(uuid.UUID(response.json()["id"]))
    return kb


async def upload(client: AsyncClient, headers: dict, data: bytes = PNG, name: str = "screen.png") -> dict:
    response = await client.post(IMAGES, files={"file": (name, data)}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def stored(root: Path, pattern: str) -> list[Path]:
    """Files under the storage root (sync helper: keeps blocking I/O out of async tests)."""
    return sorted(root.rglob(pattern))


def image_blocks(content: list | str) -> list[dict]:
    return [block for block in content if block["type"] == "image"] if isinstance(content, list) else []


# --- upload / serve -----------------------------------------------------------------


async def test_upload_and_serve_image(client: AsyncClient, alice: dict, upload_dir: Path) -> None:
    image = await upload(client, alice, JPEG, "photo.jpeg")

    assert image["media_type"] == "image/jpeg"
    assert (image["width"], image["height"]) == (64, 48)
    content = await client.get(f"{IMAGES}/{image['id']}/content", headers=alice)
    assert content.content == JPEG
    assert content.headers["content-type"] == "image/jpeg"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert [p.name for p in stored(upload_dir, "*.jpg")] == [f"{image['id']}.jpg"]


@pytest.mark.parametrize(
    ("data", "status"),
    [(b"<svg onload='alert(1)'/>", 415), (image_bytes("BMP"), 415), (b"not an image", 422), (b"", 422)],
    ids=["svg", "bmp", "garbage", "empty"],  # raw bytes as IDs exceed Windows' env-var length limit
)
async def test_invalid_uploads_are_rejected(
    client: AsyncClient, alice: dict, data: bytes, status: int
) -> None:
    response = await client.post(IMAGES, files={"file": ("x.png", data)}, headers=alice)

    assert response.status_code == status


async def test_oversized_image_is_rejected(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_image_size", 100)

    response = await client.post(IMAGES, files={"file": ("big.png", PNG)}, headers=alice)

    assert response.status_code == 413


async def test_images_are_private(client: AsyncClient, alice: dict, register_user: RegisterFn) -> None:
    image = await upload(client, alice)
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])

    assert (await client.get(f"{IMAGES}/{image['id']}/content", headers=bob)).status_code == 404
    assert (await client.delete(f"{IMAGES}/{image['id']}", headers=bob)).status_code == 404
    assert (
        await client.post(CHAT, json={"message": "hi", "image_ids": [image["id"]]}, headers=bob)
    ).status_code == 404


# --- chat -------------------------------------------------------------------------------


async def test_image_question_without_knowledge_base(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)
    llm.answer = "A dark blue rectangle."

    body = (
        await client.post(CHAT, json={"message": "What is this?", "image_ids": [image["id"]]}, headers=alice)
    ).json()

    assert body["assistant_message"]["answer_type"] == "image"
    assert body["user_message"]["images"][0]["id"] == image["id"]
    [call] = llm.answer_calls
    assert call["system"] == IMAGE_SYSTEM_PROMPT
    content = call["messages"][-1]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[-1] == {"type": "text", "text": "What is this?"}


async def test_image_only_message_uses_a_default_question(
    client: AsyncClient, alice: dict, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)

    body = (await client.post(CHAT, json={"message": "", "image_ids": [image["id"]]}, headers=alice)).json()

    assert body["user_message"]["content"] == ""
    assert llm.answer_calls[0]["messages"][-1]["content"][-1]["text"] == DEFAULT_IMAGE_QUESTION


async def test_image_with_knowledge_base_searches_with_its_visible_text(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)
    llm.rewrite = "ERR-4521 VPN error"  # what the model would read off the screenshot
    llm.cite = [(0, "Error ERR-4521 means the VPN certificate expired")]

    body = (
        await client.post(
            CHAT,
            json={"message": "How do I fix this?", "image_ids": [image["id"]], "knowledge_base_id": kb_id},
            headers=alice,
        )
    ).json()

    assistant = body["assistant_message"]
    assert assistant["answer_type"] == "multimodal"
    assert assistant["retrieval_query"] == "ERR-4521 VPN error"
    assert assistant["citations"][0]["filename"] == "vpn.md"
    [rewrite] = llm.rewrite_calls  # no history, but the image needs reading for search
    assert image_blocks(rewrite["messages"][0]["content"])
    [answer] = llm.answer_calls
    assert answer["system"] == MULTIMODAL_SYSTEM_PROMPT
    assert [block["type"] for block in answer["messages"][-1]["content"]] == ["image", "document", "text"]


async def test_image_with_uncited_documents_is_an_image_answer(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)
    llm.rewrite = "ERR-4521 VPN error"
    llm.cite = []

    body = (
        await client.post(
            CHAT,
            json={"message": "What is this?", "image_ids": [image["id"]], "knowledge_base_id": kb_id},
            headers=alice,
        )
    ).json()

    assert body["assistant_message"]["answer_type"] == "image"


async def test_image_with_nothing_relevant_in_the_documents_still_answers_from_the_image(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)
    llm.rewrite = "sourdough bread crust"  # nothing in the knowledge base matches

    body = (
        await client.post(
            CHAT,
            json={"message": "What bread is this?", "image_ids": [image["id"]], "knowledge_base_id": kb_id},
            headers=alice,
        )
    ).json()

    assert body["assistant_message"]["answer_type"] == "image"
    assert body["assistant_message"]["sources"] == []
    assert (
        llm.answer_calls[0]["system"] == IMAGE_SYSTEM_PROMPT
    )  # the model was asked, not a canned "not found"


async def test_follow_ups_keep_earlier_images_in_context_up_to_the_limit(
    client: AsyncClient, alice: dict, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_history_images", 1)
    first, second = await upload(client, alice, image_bytes("PNG", color="red")), await upload(client, alice)
    conversation_id = (
        await client.post(
            CHAT, json={"message": "Compare these", "image_ids": [first["id"], second["id"]]}, headers=alice
        )
    ).json()["conversation"]["id"]

    await client.post(
        CHAT,
        json={"message": "What about the second one?", "conversation_id": conversation_id},
        headers=alice,
    )

    history_user_turn = llm.answer_calls[-1]["messages"][0]
    assert len(image_blocks(history_user_turn["content"])) == 1  # capped by MAX_HISTORY_IMAGES
    assert history_user_turn["content"][-1]["text"] == "Compare these"


async def test_an_image_can_only_be_sent_once(client: AsyncClient, alice: dict) -> None:
    image = await upload(client, alice)
    await client.post(CHAT, json={"message": "one", "image_ids": [image["id"]]}, headers=alice)

    again = await client.post(CHAT, json={"message": "two", "image_ids": [image["id"]]}, headers=alice)
    delete = await client.delete(f"{IMAGES}/{image['id']}", headers=alice)

    assert again.status_code == 409
    assert delete.status_code == 409


async def test_too_many_images(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_images_per_message", 1)
    ids = [(await upload(client, alice))["id"] for _ in range(2)]

    response = await client.post(CHAT, json={"message": "hi", "image_ids": ids}, headers=alice)

    assert response.status_code == 400


async def test_missing_image_file_fails_loudly_and_saves_nothing(
    client: AsyncClient, alice: dict, upload_dir: Path, db: AsyncSession, llm: ScriptedLLM
) -> None:
    image = await upload(client, alice)
    stored(upload_dir, "*.png")[0].unlink()

    response = await client.post(
        CHAT, json={"message": "What is this?", "image_ids": [image["id"]]}, headers=alice
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert llm.calls == []  # never answer as if the image had been seen
    assert await db.scalar(select(func.count()).select_from(Message)) == 0


async def test_deleting_a_conversation_deletes_its_image_files(
    client: AsyncClient, alice: dict, upload_dir: Path
) -> None:
    image = await upload(client, alice)
    conversation_id = (
        await client.post(CHAT, json={"message": "x", "image_ids": [image["id"]]}, headers=alice)
    ).json()["conversation"]["id"]

    await client.delete(f"/api/v1/conversations/{conversation_id}", headers=alice)

    assert stored(upload_dir, "*.png") == []
    assert (await client.get(f"{IMAGES}/{image['id']}/content", headers=alice)).status_code == 404


async def test_unsent_image_can_be_deleted(client: AsyncClient, alice: dict, upload_dir: Path) -> None:
    image = await upload(client, alice)

    assert (await client.delete(f"{IMAGES}/{image['id']}", headers=alice)).status_code == 204
    assert stored(upload_dir, "*.png") == []


# --- one-shot endpoint ------------------------------------------------------------------


async def test_one_shot_image_question(
    client: AsyncClient, alice: dict, kb_id: str, llm: ScriptedLLM, db: AsyncSession
) -> None:
    llm.rewrite = "ERR-4521 VPN error"
    llm.cite = [(0, "Error ERR-4521 means the VPN certificate expired")]

    plain = await client.post(
        "/api/v1/multimodal/image",
        files={"file": ("a.png", PNG)},
        data={"question": "What is this?"},
        headers=alice,
    )
    grounded = await client.post(
        "/api/v1/multimodal/image",
        files={"file": ("a.png", PNG)},
        data={"question": "How do I fix this?", "knowledge_base_id": kb_id},
        headers=alice,
    )
    invalid = await client.post(
        "/api/v1/multimodal/image", files={"file": ("a.svg", b"<svg/>")}, headers=alice
    )

    assert plain.json()["answer_type"] == "image"
    assert grounded.json()["answer_type"] == "multimodal"
    assert grounded.json()["citations"][0]["filename"] == "vpn.md"
    assert invalid.status_code == 415
    assert await db.scalar(select(func.count()).select_from(Message)) == 0  # not saved to any conversation
