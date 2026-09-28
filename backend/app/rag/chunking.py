"""Split extracted text blocks into overlapping chunks for embedding.

Recursive character splitting: try to break on paragraph boundaries, then line
breaks, then sentences, then words, and only as a last resort mid-word. Pieces
are packed greedily up to `chunk_size` characters, and each new chunk starts
with up to `chunk_overlap` characters from the end of the previous one so that
facts spanning a boundary remain retrievable.

Chunks never cross a block (page or section) boundary, so each chunk has one
page number / section for citations.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.extraction import ExtractedDocument, TextBlock

SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")
_HAS_WORD = re.compile(r"\w")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str
    page_number: int | None
    section: str | None


def chunk_document(document: ExtractedDocument, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[Chunk] = []
    for block in merge_small_blocks(document.blocks, chunk_size):
        for text in split_text(block.text, chunk_size, chunk_overlap):
            chunks.append(Chunk(len(chunks), text, block.page_number, block.section))
    return chunks


def _is_same_or_subsection(parent: str | None, child: str | None) -> bool:
    if parent == child:
        return True
    return parent is not None and child is not None and child.startswith(f"{parent} > ")


def merge_small_blocks(blocks: Sequence[TextBlock], chunk_size: int) -> list[TextBlock]:
    """Fold a short block into the next one when the next is the same section or one of its
    subsections (typically a heading-only parent like "Leave Policy" followed by
    "Leave Policy > Annual leave"), so it doesn't become a tiny, low-information chunk.

    Sibling sections are never merged: each chunk's section label must stay accurate
    because it is shown in citations."""
    small = chunk_size // 4
    merged: list[TextBlock] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.page_number == block.page_number
            and len(previous.text) < small
            and len(previous.text) + len(block.text) + 2 <= chunk_size
            and _is_same_or_subsection(previous.section, block.section)
        ):
            merged[-1] = TextBlock(
                text=f"{previous.text}\n\n{block.text}",
                page_number=previous.page_number,
                section=block.section,  # the more specific heading path
            )
        else:
            merged.append(block)
    return merged


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    pieces = _split_recursive(text, chunk_size, SEPARATORS)
    chunks = _pack(pieces, chunk_size, chunk_overlap)
    return [chunk for chunk in chunks if _HAS_WORD.search(chunk)]


def _split_recursive(text: str, max_len: int, separators: Sequence[str]) -> list[str]:
    """Break text into pieces no longer than max_len, keeping separators attached."""
    if len(text) <= max_len:
        return [text]
    for position, separator in enumerate(separators):
        if separator not in text:
            continue
        parts = text.split(separator)
        # Re-attach the separator to each part except the last so no text is lost.
        parts = [part + separator for part in parts[:-1]] + [parts[-1]]
        result: list[str] = []
        for part in parts:
            if not part:
                continue
            if len(part) <= max_len:
                result.append(part)
            else:
                result.extend(_split_recursive(part, max_len, separators[position + 1 :]))
        return result
    # No separator left (e.g. a very long URL): hard-split.
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _pack(pieces: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for piece in pieces:
        if current and length + len(piece) > chunk_size:
            chunks.append("".join(current).strip())
            # Carry trailing pieces forward as overlap, within the overlap budget
            # and leaving room for the incoming piece.
            while current and (length > chunk_overlap or length + len(piece) > chunk_size):
                length -= len(current.pop(0))
        current.append(piece)
        length += len(piece)

    if current:
        tail = "".join(current).strip()
        # Skip a final chunk that is nothing but overlap already emitted.
        if tail and (not chunks or not chunks[-1].endswith(tail)):
            chunks.append(tail)
    return chunks
