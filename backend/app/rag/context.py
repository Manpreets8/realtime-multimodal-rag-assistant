"""Post-retrieval steps shared by the answer pipeline and the evaluation harness, so what the
evaluation measures is exactly what answers use.

- `deduplicate`: drop near-duplicate passages (the same text in two documents, e.g. two versions
  of a policy) so they don't take several of the few context slots.
- `select_context`: after reranking, drop passages below the relevance threshold (if one is
  configured), then keep the best ones up to RERANK_TOP_K and a character budget.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only: retrieval imports this module
    from app.rag.reranking import RankedChunk
    from app.rag.retrieval import RetrievedChunk

_WORD = re.compile(r"\w+")
SHINGLE_SIZE = 3


def _shingles(text: str) -> frozenset[str]:
    words = _WORD.findall(text.lower())
    if len(words) <= SHINGLE_SIZE:
        return frozenset([" ".join(words)])
    return frozenset(" ".join(words[i : i + SHINGLE_SIZE]) for i in range(len(words) - SHINGLE_SIZE + 1))


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two shingle sets (1.0 = the same word sequences)."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate(
    chunks: Sequence["RetrievedChunk"], threshold: float | None
) -> tuple[list["RetrievedChunk"], int]:
    """Keep chunks in order, dropping any whose 3-word shingles overlap a kept chunk's by at least
    `threshold` (Jaccard). Adjacent chunks of one document share only their overlap region, far
    below the default 0.9, so they are not affected. `None` disables. Returns (kept, removed)."""
    if threshold is None:
        return list(chunks), 0
    kept: list[RetrievedChunk] = []
    kept_shingles: list[frozenset[str]] = []
    for chunk in chunks:
        shingles = _shingles(chunk.content)
        if any(similarity(shingles, other) >= threshold for other in kept_shingles):
            continue
        kept.append(chunk)
        kept_shingles.append(shingles)
    return kept, len(chunks) - len(kept)


@dataclass(slots=True)
class ContextSelection:
    passages: list["RankedChunk"] = field(default_factory=list)
    below_threshold: int = 0  # dropped: rerank score under RERANK_MIN_SCORE
    over_budget: int = 0  # dropped: would exceed CONTEXT_MAX_CHARS
    beyond_top_k: int = 0  # relevant, but RERANK_TOP_K passages were already chosen
    characters: int = 0


def select_context(
    ranked: Sequence["RankedChunk"], *, top_k: int, min_rerank_score: float | None, max_chars: int
) -> ContextSelection:
    """Choose the passages sent to the model from reranked candidates (best first). The first
    passage is always kept if it clears the threshold, even if it alone exceeds the budget."""
    selection = ContextSelection()
    for item in ranked:
        if (
            min_rerank_score is not None
            and item.rerank_score is not None
            and item.rerank_score < min_rerank_score
        ):
            selection.below_threshold += 1
            continue
        if len(selection.passages) >= top_k:
            selection.beyond_top_k += 1
            continue
        length = len(item.chunk.content)
        if selection.passages and selection.characters + length > max_chars:
            selection.over_budget += 1
            continue
        selection.passages.append(item)
        selection.characters += length
    return selection
