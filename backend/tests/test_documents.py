import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document
from tests.conftest import RegisterFn, bearer
from tests.samples import DOCX, MARKDOWN, PDF, TXT

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.fixture
async def bob(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="bob@example.com"))["access_token"])


@pytest.fixture
async def kb_id(client: AsyncClient, alice: dict) -> str:
    response = await client.post("/api/v1/knowledge-bases", json={"name": "Handbook"}, headers=alice)
    return response.json()["id"]


async def upload(client: AsyncClient, headers: dict, kb: str, filename: str, content: bytes):
    return await client.post(
        f"{DOCS}/upload", data={"knowledge_base_id": kb}, files={"file": (filename, content)}, headers=headers
    )


def stored_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("handbook.pdf", PDF, "application/pdf"),
        ("report.DOCX", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("notes.txt", TXT, "text/plain"),
        ("readme.md", MARKDOWN, "text/markdown"),
        ("bom.txt", b"\xef\xbb\xbfUTF-8 with BOM", "text/plain"),
        ("utf16.txt", "UTF-16 text".encode("utf-16"), "text/plain"),
    ],
)
async def test_upload_supported_types(
    client: AsyncClient,
    alice: dict,
    kb_id: str,
    db: AsyncSession,
    upload_dir: Path,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    response = await upload(client, alice, kb_id, filename, content)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == filename
    assert body["status"] == "uploaded"
    assert body["content_type"] == content_type
    assert body["size_bytes"] == len(content)
    assert "storage_key" not in body

    document = await db.scalar(select(Document).where(Document.id == uuid.UUID(body["id"])))
    assert document is not None
    stored = upload_dir / document.storage_key
    assert stored.read_bytes() == content
    assert stored.name == f"{document.id}{document.extension}"  # user filename never reaches the disk


async def test_path_traversal_filename_is_neutralised(
    client: AsyncClient, alice: dict, kb_id: str, upload_dir: Path, tmp_path: Path
) -> None:
    response = await upload(client, alice, kb_id, "../../../evil/passwd.txt", TXT)

    assert response.status_code == 201
    assert response.json()["filename"] == "passwd.txt"
    files = stored_files(tmp_path)
    assert len(files) == 1
    assert files[0].is_relative_to(upload_dir)


@pytest.mark.parametrize(
    ("filename", "content", "status", "code"),
    [
        ("malware.exe", b"MZ\x90\x00", 415, "unsupported_file_type"),
        ("no_extension", TXT, 415, "unsupported_file_type"),
        ("fake.pdf", b"just some text pretending", 422, "invalid_document"),
        ("fake.docx", b"PK\x03\x04 not really a zip", 422, "invalid_document"),
        ("binary.txt", b"abc\x00\x01\x02", 422, "invalid_document"),
        ("latin1.md", "café".encode("latin-1"), 422, "invalid_document"),
        ("empty.txt", b"", 422, "invalid_document"),
    ],
)
async def test_invalid_uploads_are_rejected_and_leave_no_files(
    client: AsyncClient,
    alice: dict,
    kb_id: str,
    upload_dir: Path,
    filename: str,
    content: bytes,
    status: int,
    code: str,
) -> None:
    response = await upload(client, alice, kb_id, filename, content)

    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert stored_files(upload_dir) == []


async def test_docx_without_document_xml_is_rejected(client: AsyncClient, alice: dict, kb_id: str) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "a zip, but not a Word document")

    response = await upload(client, alice, kb_id, "archive.docx", buffer.getvalue())

    assert response.status_code == 422


async def test_file_larger_than_limit_is_rejected(
    client: AsyncClient, alice: dict, kb_id: str, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_file_size", 1000)

    response = await upload(client, alice, kb_id, "big.txt", b"a" * 1001)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert stored_files(upload_dir) == []


async def test_oversized_request_body_is_rejected_before_parsing(
    client: AsyncClient, alice: dict, kb_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_file_size", 1000)

    # Larger than max_file_size + multipart overhead, so the middleware rejects it on Content-Length.
    response = await upload(client, alice, kb_id, "huge.txt", b"a" * (2 * 1024 * 1024))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["X-Request-ID"]


async def test_duplicate_content_is_rejected_within_a_knowledge_base_only(
    client: AsyncClient, alice: dict, kb_id: str
) -> None:
    first = await upload(client, alice, kb_id, "policy.pdf", PDF)
    duplicate = await upload(client, alice, kb_id, "renamed-copy.pdf", PDF)
    other_kb = (await client.post("/api/v1/knowledge-bases", json={"name": "Other"}, headers=alice)).json()[
        "id"
    ]
    elsewhere = await upload(client, alice, other_kb, "policy.pdf", PDF)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert "policy.pdf" in duplicate.json()["error"]["message"]
    assert elsewhere.status_code == 201


async def test_list_get_and_download(client: AsyncClient, alice: dict, kb_id: str) -> None:
    await upload(client, alice, kb_id, "first.txt", b"first file")
    second = (await upload(client, alice, kb_id, "second report.md", MARKDOWN)).json()

    listing = (await client.get(f"/api/v1/knowledge-bases/{kb_id}/documents", headers=alice)).json()
    detail = await client.get(f"{DOCS}/{second['id']}", headers=alice)
    download = await client.get(f"{DOCS}/{second['id']}/download", headers=alice)

    assert [d["filename"] for d in listing] == ["second report.md", "first.txt"]  # newest first
    assert detail.json()["id"] == second["id"]
    assert download.status_code == 200
    assert download.content == MARKDOWN
    assert download.headers["content-type"].startswith("text/markdown")
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"


async def test_delete_document_removes_row_and_file(
    client: AsyncClient, alice: dict, kb_id: str, upload_dir: Path
) -> None:
    document = (await upload(client, alice, kb_id, "gone.pdf", PDF)).json()

    response = await client.delete(f"{DOCS}/{document['id']}", headers=alice)

    assert response.status_code == 204
    assert (await client.get(f"{DOCS}/{document['id']}", headers=alice)).status_code == 404
    assert stored_files(upload_dir) == []


@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("GET", "/download"), ("DELETE", "")])
async def test_other_users_document_is_not_found(
    client: AsyncClient, alice: dict, bob: dict, kb_id: str, method: str, suffix: str
) -> None:
    document = (await upload(client, alice, kb_id, "private.pdf", PDF)).json()

    response = await client.request(method, f"{DOCS}/{document['id']}{suffix}", headers=bob)

    assert response.status_code == 404
    assert (await client.get(f"{DOCS}/{document['id']}", headers=alice)).status_code == 200


async def test_cannot_upload_into_another_users_knowledge_base(
    client: AsyncClient, bob: dict, kb_id: str, upload_dir: Path
) -> None:
    response = await upload(client, bob, kb_id, "intruder.pdf", PDF)

    assert response.status_code == 404
    assert stored_files(upload_dir) == []


async def test_upload_requires_authentication(client: AsyncClient, kb_id: str) -> None:
    response = await upload(client, {}, kb_id, "anon.pdf", PDF)

    assert response.status_code == 401


async def test_upload_config(client: AsyncClient, alice: dict) -> None:
    body = (await client.get(f"{DOCS}/upload-config", headers=alice)).json()

    assert body["max_file_size"] == get_settings().max_file_size
    assert {t["extension"] for t in body["supported_types"]} == {".pdf", ".docx", ".txt", ".md", ".markdown"}


async def test_chunked_upload_without_content_length_is_cut_off(
    client: AsyncClient, alice: dict, kb_id: str, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_file_size", 1000)
    boundary = "testboundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="knowledge_base_id"\r\n\r\n{kb_id}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="stream.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode()

    async def body():  # an async generator makes httpx use chunked transfer encoding
        yield head
        for _ in range(3):
            yield b"a" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    response = await client.post(
        f"{DOCS}/upload",
        content=body(),
        headers={**alice, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert stored_files(upload_dir) == []
