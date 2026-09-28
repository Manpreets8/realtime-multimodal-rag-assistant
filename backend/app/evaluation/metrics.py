"""Evaluation metrics. Pure functions over retrieved chunks and answers, so every number in
a report can be recomputed from the per-question details saved alongside it.

Retrieval (no LLM needed):
- hit@k: at least one relevant chunk in the top k.
- recall@k: share of the question's evidence items found in the top k.
- MRR: 1 / rank of the first relevant chunk (0 if none was retrieved).
- nDCG@k: rank-aware; a chunk gains only if it adds evidence not already found, so
  near-duplicate chunks can't inflate the score and the ideal is always reachable.

Answers:
- fact recall: share of expected facts the answer states (deterministic string checks).
- abstention: unanswerable questions should get the "not found" answer, answerable ones not.
- citation checks: quotes verified verbatim in the cited source, cited sources relevant,
  and the question's evidence covered by the citations.
"""

import math
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.evaluation.dataset import Evidence

# Curly quotes and non-breaking spaces, as produced by word processors and PDF extraction.
_QUOTES = str.maketrans(
    {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"', chr(0xA0): " "}
)


def normalize(text: str) -> str:
    return " ".join(text.translate(_QUOTES).lower().split())


@dataclass(frozen=True, slots=True)
class Passage:
    """A retrieved or cited chunk, as far as the metrics are concerned."""

    filename: str
    content: str


def covers(passage: Passage, evidence: Evidence) -> bool:
    return passage.filename == evidence.document and normalize(evidence.evidence) in normalize(
        passage.content
    )


def covered_evidence(passage: Passage, evidence: Sequence[Evidence]) -> set[int]:
    return {i for i, item in enumerate(evidence) if covers(passage, item)}


@dataclass(slots=True)
class RankingScores:
    hit: dict[int, float]
    recall: dict[int, float]
    ndcg: dict[int, float]
    mrr: float
    first_relevant_rank: int | None
    relevant_ranks: list[int]


def score_ranking(
    ranked: Sequence[Passage], evidence: Sequence[Evidence], ks: Iterable[int]
) -> RankingScores:
    """Scores for one answerable question. `ranked` is best-first."""
    if not evidence:
        raise ValueError("score_ranking needs at least one evidence item")
    coverage = [covered_evidence(passage, evidence) for passage in ranked]
    relevant_ranks = [rank for rank, found in enumerate(coverage, start=1) if found]
    first = relevant_ranks[0] if relevant_ranks else None

    # Gains for nDCG: 1 when a chunk covers evidence not seen higher up.
    gains, seen = [], set()
    for found in coverage:
        new = found - seen
        gains.append(1.0 if new else 0.0)
        seen |= found

    hit, recall, ndcg = {}, {}, {}
    for k in ks:
        top = coverage[:k]
        found_at_k = set().union(*top) if top else set()
        hit[k] = 1.0 if any(top) else 0.0
        recall[k] = len(found_at_k) / len(evidence)
        dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))
        ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(evidence))))
        ndcg[k] = dcg / ideal
    return RankingScores(hit, recall, ndcg, 1 / first if first else 0.0, first, relevant_ranks)


_HAS_DIGIT = re.compile(r"\d")


def states_fact(answer: str, variants: Sequence[str]) -> bool:
    """Whether the answer states a fact in one of its accepted phrasings. Numbers must not be
    part of a longer number ("5" doesn't match "15" or "5.5"); words match as substrings
    ("anonym" matches "anonymously")."""
    text = normalize(answer)
    for variant in variants:
        needle = normalize(variant)
        if _HAS_DIGIT.search(needle):
            if re.search(rf"(?<![\d.,]){re.escape(needle)}(?![\d]|[.,]\d)", text):
                return True
        elif needle in text:
            return True
    return False


def fact_recall(answer: str, facts: Sequence[Sequence[str]]) -> float | None:
    if not facts:
        return None
    return sum(states_fact(answer, variants) for variants in facts) / len(facts)


@dataclass(slots=True)
class CitedSource:
    passage: Passage
    quotes_total: int
    quotes_verified: int  # quotes located verbatim in the source text


@dataclass(slots=True)
class CitationScores:
    cited: bool
    quote_verification: float | None  # verified quotes / quotes
    cited_source_relevance: float | None  # cited sources that are labelled relevant / cited sources
    evidence_citation_recall: float | None  # evidence items covered by a cited source / evidence items


def score_citations(sources: Sequence[CitedSource], evidence: Sequence[Evidence]) -> CitationScores:
    if not sources:
        return CitationScores(False, None, None, 0.0 if evidence else None)
    quotes = sum(s.quotes_total for s in sources)
    verified = sum(s.quotes_verified for s in sources)
    relevant = [bool(covered_evidence(s.passage, evidence)) for s in sources]
    covered = set().union(*(covered_evidence(s.passage, evidence) for s in sources))
    return CitationScores(
        cited=True,
        quote_verification=verified / quotes if quotes else None,
        cited_source_relevance=sum(relevant) / len(relevant) if evidence else None,
        evidence_citation_recall=len(covered) / len(evidence) if evidence else None,
    )


# --- aggregation -------------------------------------------------------------------------------


def mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 4) if present else None


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile (no interpolation), e.g. pct=95."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(pct / 100 * len(ordered)), 1)
    return round(ordered[rank - 1], 2)
