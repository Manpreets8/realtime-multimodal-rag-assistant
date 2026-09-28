import uuid

import pymupdf
import pytest
from httpx import AsyncClient

from app.services.ingestion_service import process_document
from tests.conftest import RegisterFn, bearer

pytestmark = pytest.mark.integration


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


async def indexed(client: AsyncClient, headers: dict, filename: str, content: bytes) -> dict:
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": filename}, headers=headers)).json()["id"]
    document = (
        await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": (filename, content)},
            headers=headers,
        )
    ).json()
    await process_document(uuid.UUID(document["id"]))
    return document


def five_page_pdf() -> bytes:
    pdf = pymupdf.open()
    for number in range(1, 6):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Page {number} explains policy number {number}.")
    return pdf.tobytes()


async def test_chunk_is_returned_with_its_neighbours(client: AsyncClient, alice: dict) -> None:
    document = await indexed(client, alice, "handbook.pdf", five_page_pdf())
    chunks = (await client.get(f"/api/v1/documents/{document['id']}/chunks", headers=alice)).json()
    middle = chunks[2]

    body = (await client.get(f"/api/v1/chunks/{middle['id']}", headers=alice)).json()

    assert body["chunk"]["content"] == middle["content"]
    assert body["chunk"]["page_number"] == 3
    assert [c["page_number"] for c in body["before"]] == [2]
    assert [c["page_number"] for c in body["after"]] == [4]
    assert body["document"]["filename"] == "handbook.pdf"
    assert body["document"]["status"] == "completed"


async def test_neighbour_count_is_configurable_and_clipped_at_the_edges(
    client: AsyncClient, alice: dict
) -> None:
    document = await indexed(client, alice, "handbook.pdf", five_page_pdf())
    first = (await client.get(f"/api/v1/documents/{document['id']}/chunks", headers=alice)).json()[0]

    wide = (await client.get(f"/api/v1/chunks/{first['id']}", params={"neighbors": 3}, headers=alice)).json()
    none = (await client.get(f"/api/v1/chunks/{first['id']}", params={"neighbors": 0}, headers=alice)).json()

    assert wide["before"] == []
    assert [c["chunk_index"] for c in wide["after"]] == [1, 2, 3]
    assert none["before"] == none["after"] == []
    assert (
        await client.get(f"/api/v1/chunks/{first['id']}", params={"neighbors": 4}, headers=alice)
    ).status_code == 422


async def test_chunks_are_private(client: AsyncClient, alice: dict, register_user: RegisterFn) -> None:
    document = await indexed(client, alice, "notes.txt", b"Private notes.")
    chunk = (await client.get(f"/api/v1/documents/{document['id']}/chunks", headers=alice)).json()[0]
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])

    assert (await client.get(f"/api/v1/chunks/{chunk['id']}", headers=bob)).status_code == 404
    assert (await client.get(f"/api/v1/chunks/{uuid.uuid4()}", headers=alice)).status_code == 404
    assert (await client.get(f"/api/v1/chunks/{chunk['id']}")).status_code == 401


async def test_pdfs_can_be_displayed_inline_but_other_types_always_download(
    client: AsyncClient, alice: dict
) -> None:
    pdf = await indexed(client, alice, "handbook.pdf", five_page_pdf())
    text = await indexed(client, alice, "page.txt", b"<script>alert(1)</script> is just text")

    pdf_inline = await client.get(
        f"/api/v1/documents/{pdf['id']}/download", params={"inline": True}, headers=alice
    )
    pdf_default = await client.get(f"/api/v1/documents/{pdf['id']}/download", headers=alice)
    text_inline = await client.get(
        f"/api/v1/documents/{text['id']}/download", params={"inline": True}, headers=alice
    )

    assert pdf_inline.headers["content-disposition"].startswith("inline")
    assert pdf_inline.headers["content-type"] == "application/pdf"
    assert pdf_default.headers["content-disposition"].startswith("attachment")
    assert text_inline.headers["content-disposition"].startswith("attachment")
    assert text_inline.headers["x-content-type-options"] == "nosniff"
