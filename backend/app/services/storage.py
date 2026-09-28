"""File storage for uploaded documents.

Keys are server-generated relative paths (``{user_id}/{kb_id}/{document_id}.pdf``).
The interface is small on purpose so an S3/GCS implementation can replace the
local one without touching callers.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.core.config import get_settings
from app.core.errors import FileTooLargeError


@dataclass(frozen=True, slots=True)
class StoredFile:
    size_bytes: int
    sha256: str


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Defence in depth: keys are generated server-side, but never allow escaping the root.
        if not path.is_relative_to(self.root):
            raise ValueError(f"Storage key escapes the storage root: {key!r}")
        return path

    async def write_stream(self, key: str, chunks: AsyncIterator[bytes], max_bytes: int) -> StoredFile:
        """Stream chunks to disk, hashing as we go; abort (and clean up) past `max_bytes`."""
        path = anyio.Path(self.path_for(key))
        await path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(path, "wb") as file:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLargeError(
                            f"The file exceeds the maximum size of {max_bytes // (1024 * 1024)} MB."
                        )
                    digest.update(chunk)
                    await file.write(chunk)
        except BaseException:
            await path.unlink(missing_ok=True)
            raise
        return StoredFile(size_bytes=size, sha256=digest.hexdigest())

    async def move(self, source_key: str, target_key: str) -> None:
        await anyio.Path(self.path_for(source_key)).replace(self.path_for(target_key))

    async def delete(self, key: str) -> None:
        await anyio.Path(self.path_for(key)).unlink(missing_ok=True)

    async def write_bytes(self, key: str, data: bytes) -> None:
        path = anyio.Path(self.path_for(key))
        await path.parent.mkdir(parents=True, exist_ok=True)
        await path.write_bytes(data)

    async def read_bytes(self, key: str) -> bytes:
        return await anyio.Path(self.path_for(key)).read_bytes()

    async def exists(self, key: str) -> bool:
        return await anyio.Path(self.path_for(key)).is_file()


def get_storage() -> LocalFileStorage:
    return LocalFileStorage(get_settings().upload_dir)
