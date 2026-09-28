from pathlib import Path

import pytest

from app.core.errors import InvalidDocumentError, UnsupportedFileTypeError
from app.utils.files import (
    MAX_FILENAME_LENGTH,
    SUPPORTED_FILE_TYPES,
    resolve_file_type,
    sanitize_filename,
    validate_file_content,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\notes.md", "notes.md"),
        ("bad\x00name\x1f.txt", "badname.txt"),
        ("   spaced.txt  ", "spaced.txt"),
        ("", "document"),
        (None, "document"),
        ("..", "document"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension() -> None:
    name = sanitize_filename("a" * 400 + ".pdf")

    assert len(name) == MAX_FILENAME_LENGTH
    assert name.endswith(".pdf")


def test_resolve_file_type_is_case_insensitive() -> None:
    assert resolve_file_type("Scan.PDF") is SUPPORTED_FILE_TYPES[".pdf"]


@pytest.mark.parametrize("filename", ["image.png", "archive.zip", "script.py", "noext"])
def test_resolve_file_type_rejects_unsupported(filename: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        resolve_file_type(filename)


def test_multibyte_character_split_at_sniff_boundary_is_valid(tmp_path: Path) -> None:
    # 1 MiB sample boundary falls inside a 3-byte UTF-8 character.
    content = b"a" * (1024 * 1024 - 1) + "\u20ac".encode() + b" tail"
    path = tmp_path / "boundary.txt"
    path.write_bytes(content)

    validate_file_content(path, SUPPORTED_FILE_TYPES[".txt"])


def test_invalid_utf8_in_text_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"valid start \xff\xfe broken")

    with pytest.raises(InvalidDocumentError):
        validate_file_content(path, SUPPORTED_FILE_TYPES[".txt"])
