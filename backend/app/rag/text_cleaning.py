"""Normalise extracted text before chunking and embedding."""

import re
import unicodedata

# Zero-width characters, soft hyphens and BOMs that survive extraction but carry no meaning.
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def clean_text(text: str, *, dehyphenate: bool = False) -> str:
    """
    - NFKC-normalise (PDF ligatures like "ﬁ" become "fi", full-width forms become ASCII).
    - Drop control and zero-width characters (keeping newlines and tabs as whitespace).
    - Optionally re-join words hyphenated across line breaks (PDF layout artefact).
    - Collapse runs of spaces, trim lines, and keep at most one blank line between paragraphs.
    """
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_INVISIBLE)
    text = "".join(ch if ch in "\n\t" or not unicodedata.category(ch).startswith("C") else " " for ch in text)
    if dehyphenate:
        text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _HORIZONTAL_SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _EXCESS_NEWLINES.sub("\n\n", text).strip()
