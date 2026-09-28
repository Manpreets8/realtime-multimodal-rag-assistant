"""Filename sanitisation and content-based file-type validation.

The extension decides which parser will run in Phase 4, so we verify the bytes
actually match it instead of trusting the extension or the client's Content-Type.
"""

import codecs
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.errors import InvalidDocumentError, UnsupportedFileTypeError

MAX_FILENAME_LENGTH = 255
_TEXT_SNIFF_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileType:
    extension: str
    content_type: str
    label: str


SUPPORTED_FILE_TYPES: dict[str, FileType] = {
    ".pdf": FileType(".pdf", "application/pdf", "PDF"),
    ".docx": FileType(
        ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Word"
    ),
    ".txt": FileType(".txt", "text/plain", "Text"),
    ".md": FileType(".md", "text/markdown", "Markdown"),
    ".markdown": FileType(".markdown", "text/markdown", "Markdown"),
}


def sanitize_filename(raw: str | None) -> str:
    """Return a display-safe filename: no path parts, no control characters, bounded length."""
    name = (raw or "").replace("\\", "/")
    name = PurePosixPath(name).name
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C")).strip()
    if not name or set(name) == {"."}:
        return "document"
    if len(name) > MAX_FILENAME_LENGTH:
        suffix = PurePosixPath(name).suffix[:16]
        name = name[: MAX_FILENAME_LENGTH - len(suffix)] + suffix
    return name


def resolve_file_type(filename: str) -> FileType:
    extension = PurePosixPath(filename).suffix.lower()
    file_type = SUPPORTED_FILE_TYPES.get(extension)
    if file_type is None:
        supported = ", ".join(sorted({t.label for t in SUPPORTED_FILE_TYPES.values()}))
        raise UnsupportedFileTypeError(
            f"'{extension or filename}' files are not supported. Upload one of: {supported}."
        )
    return file_type


def validate_file_content(path: Path, file_type: FileType) -> None:
    """Check the file's bytes match its declared type. Blocking: call from a worker thread."""
    if file_type.extension == ".pdf":
        with path.open("rb") as file:
            head = file.read(1024)
        if b"%PDF-" not in head:
            raise InvalidDocumentError("This file has a .pdf extension but is not a PDF document.")
    elif file_type.extension == ".docx":
        _validate_docx(path)
    else:
        _validate_text(path)


def _validate_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                raise InvalidDocumentError("This file is not a valid Word (.docx) document.")
    except zipfile.BadZipFile as exc:
        raise InvalidDocumentError("This file is not a valid Word (.docx) document.") from exc


def _validate_text(path: Path) -> None:
    with path.open("rb") as file:
        sample = file.read(_TEXT_SNIFF_BYTES)
        at_eof = not file.read(1)

    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
        if b"\x00" in sample:
            raise InvalidDocumentError("This file appears to be binary, not text.")

    # Incremental decoder: a multi-byte character split at the sample boundary is not an error.
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    try:
        decoder.decode(sample, final=at_eof)
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("Text files must be UTF-8 (or UTF-16 with a BOM) encoded.") from exc
