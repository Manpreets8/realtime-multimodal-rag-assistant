"""Hybrid retrieval over indexed document chunks.

    query -> normalise -> [metadata filters] -> embed
          ─┬─> vector search (pgvector HNSW, cosine) ─┐
           └─> keyword search (Postgres full text)   ─┴─> RRF fusion
          -> relevance filter -> near-duplicate removal -> top N

Every search is scoped to the requesting user's own knowledge bases. Results
carry the per-retriever scores and ranks, and the result carries per-stage
timings, so a poor answer can be traced back to what retrieval returned.
"""

import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, Uuid, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk, DocumentStatus
from app.rag.context import deduplicate
from app.rag.embeddings import EmbeddingProvider, embed_query_cached

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 2000
# Standard RRF constant (Cormack et al., 2009): damps the influence of top ranks
# so that agreement between retrievers matters more than either one's top pick.
RRF_K = 60


class FusionMethod(StrEnum):
    RRF = "rrf"  # reciprocal rank fusion: ranks only, no score normalisation (the default)
    WEIGHTED = "weighted"  # alpha * normalised similarity + (1 - alpha) * normalised keyword rank


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Restrict a search to some of the knowledge bases' documents. Empty fields don't filter."""

    document_ids: tuple[uuid.UUID, ...] = ()
    extensions: tuple[str, ...] = ()  # e.g. (".pdf", ".md")
    uploaded_after: datetime | None = None
    uploaded_before: datetime | None = None

    @property
    def active(self) -> bool:
        return bool(self.document_ids or self.extensions or self.uploaded_after or self.uploaded_before)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    chunk_index: int
    page_number: int | None
    section: str | None
    content: str
    score: float  # fused score (RRF or weighted), or the single retriever's score in vector/keyword mode
    similarity: float | None  # cosine similarity to the query, 0..1 for normalised embeddings
    keyword_score: float | None  # ts_rank_cd, when the chunk matched the keyword search
    vector_rank: int | None
    keyword_rank: int | None


@dataclass(slots=True)
class RetrievalResult:
    query: str
    mode: SearchMode
    chunks: list[RetrievedChunk]
    vector_candidates: int = 0
    keyword_candidates: int = 0
    filtered_out: int = 0
    duplicates_removed: int = 0
    # With metadata filters: how many documents matched them (None: no filters).
    filter_documents: int | None = None
    embedding_cached: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)


def normalize_query(query: str) -> str:
    """Collapse whitespace and cap the length. Returns '' for a blank query."""
    return " ".join(query.split())[:MAX_QUERY_LENGTH]


_VERB = r"(?:find|show|search|look|list|get|give|fetch|pull)(?:\s+(?:me|up|for|out))*\s+"
_ALL = r"(?:all|every|any)(?:thing)?\s+"
_SOURCES = (
    r"(?:the\s+|my\s+)?"
    r"(?:documents?|docs|passages?|results?|info(?:rmation)?|notes?|files?|content|material)\s+"
)
# A request word must come first, so a topic that merely starts with "for" or "on" is kept.
_REQUEST_PHRASING = re.compile(
    rf"""^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?
    (?:{_VERB}(?:{_ALL})?(?:{_SOURCES})? | {_ALL}(?:{_SOURCES})? | {_SOURCES})
    (?:(?:that\s+(?:is|are|mentions?)\s+)?(?:related|relating|relevant)\s+to|about|on|regarding|concerning|mentioning|for)\s+""",
    re.IGNORECASE | re.VERBOSE,
)


def topic_of(query: str) -> str:
    """The topic of a search phrased as a request: "Find everything related to YOLO object
    detection" -> "YOLO object detection". The reranker scores how well a passage answers the
    text it is given, and request phrasing drags scores down (measured: 7.91 -> 1.65 for the
    same passage). Queries that aren't phrased as requests are returned unchanged."""
    query = normalize_query(query)
    topic = _REQUEST_PHRASING.sub("", query, count=1).rstrip(" ?.!")
    return topic if topic and topic != query.rstrip(" ?.!") else query


def reciprocal_rank_fusion(rankings: Sequence[Sequence[uuid.UUID]], k: int = RRF_K) -> dict[uuid.UUID, float]:
    """score(d) = sum over rankings of 1 / (k + rank(d)), ranks starting at 1."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def _min_max(scores: dict[uuid.UUID, float]) -> dict[uuid.UUID, float]:
    """Scale to 0..1 within this result list (all equal -> 1.0)."""
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    if high == low:
        return dict.fromkeys(scores, 1.0)
    return {key: (value - low) / (high - low) for key, value in scores.items()}


def weighted_fusion(
    vector_hits: Sequence[tuple[uuid.UUID, float]],
    keyword_hits: Sequence[tuple[uuid.UUID, float]],
    alpha: float,
) -> dict[uuid.UUID, float]:
    """Convex combination of min-max normalised scores: alpha weights semantic similarity,
    1 - alpha keyword relevance. A chunk missing from one list gets 0 for it. Unlike RRF this
    uses the score gaps, not just the order, which is why it needs normalising: cosine
    similarity and ts_rank_cd are on unrelated scales."""
    vector = _min_max(dict(vector_hits))
    keyword = _min_max(dict(keyword_hits))
    return {
        chunk_id: alpha * vector.get(chunk_id, 0.0) + (1 - alpha) * keyword.get(chunk_id, 0.0)
        for chunk_id in vector.keys() | keyword.keys()
    }


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


async def _vector_search(
    db: AsyncSession,
    user_id: uuid.UUID,
    kb_ids: Sequence[uuid.UUID],
    query_vector: list[float],
    limit: int,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, float]]:
    # ef_search must be >= LIMIT for HNSW to return enough rows; iterative scans (pgvector >= 0.8)
    # keep searching the graph when the knowledge-base filter discards candidates.
    # SET can't take bind parameters; the value is always an integer built here.
    await db.execute(text(f"SET LOCAL hnsw.ef_search = {int(max(40, limit * 2))}"))
    await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    query = select(DocumentChunk.id, distance.label("distance")).where(
        DocumentChunk.user_id == user_id, DocumentChunk.knowledge_base_id.in_(kb_ids)
    )
    if document_ids is not None:
        query = query.where(DocumentChunk.document_id.in_(document_ids))
    rows = await db.execute(query.order_by(distance).limit(limit))
    # relaxed_order may return slightly out-of-order rows; re-sort exactly.
    return sorted(((row.id, 1.0 - row.distance) for row in rows), key=lambda item: -item[1])


# Natural-language questions rarely contain every word of the answer, so we OR the
# query's lexemes instead of Postgres's default AND. Lexemes come from the same
# 'english' parser as the indexed tsvector (stemming, stop-word removal) and are
# quoted, then combined with the 'simple' config so they are not stemmed twice.
#
# ts_rank_cd has no IDF, so a common query word can outrank a rare decisive one. BM25-style
# IDF weighting was tried and measured worse overall (evaluation/results/
# experiment-idf-keyword-ranking.md); hybrid fusion with vector search covers these cases.
_KEYWORD_SQL_TEMPLATE = """
    WITH q AS (
        SELECT to_tsquery('simple', coalesce(string_agg(quote_literal(lexeme), ' | '), '')) AS query
        FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS lexeme
    )
    SELECT c.id, ts_rank_cd(c.content_tsv, q.query, 32) AS rank
    FROM document_chunks AS c, q
    WHERE c.user_id = :user_id
      AND c.knowledge_base_id = ANY(:kb_ids)
      {document_filter}
      AND c.content_tsv @@ q.query
    ORDER BY rank DESC, c.id
    LIMIT :limit
    """
_KEYWORD_SQL = text(_KEYWORD_SQL_TEMPLATE.format(document_filter="")).bindparams(
    bindparam("kb_ids", type_=ARRAY(Uuid)), bindparam("user_id", type_=Uuid)
)
_KEYWORD_SQL_FILTERED = text(
    _KEYWORD_SQL_TEMPLATE.format(document_filter="AND c.document_id = ANY(:document_ids)")
).bindparams(
    bindparam("kb_ids", type_=ARRAY(Uuid)),
    bindparam("user_id", type_=Uuid),
    bindparam("document_ids", type_=ARRAY(Uuid)),
)


async def _keyword_search(
    db: AsyncSession,
    user_id: uuid.UUID,
    kb_ids: Sequence[uuid.UUID],
    query: str,
    limit: int,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, float]]:
    params = {"query": query, "user_id": user_id, "kb_ids": list(kb_ids), "limit": limit}
    if document_ids is None:
        rows = await db.execute(_KEYWORD_SQL, params)
    else:
        rows = await db.execute(_KEYWORD_SQL_FILTERED, {**params, "document_ids": list(document_ids)})
    return [(row.id, float(row.rank)) for row in rows]


async def _filtered_documents(
    db: AsyncSession, user_id: uuid.UUID, kb_ids: Sequence[uuid.UUID], filters: RetrievalFilters
) -> list[uuid.UUID]:
    """The user's indexed documents in `kb_ids` that match the filters. IDs of other users'
    documents simply match nothing."""
    query = select(Document.id).where(
        Document.user_id == user_id,
        Document.knowledge_base_id.in_(kb_ids),
        Document.status == DocumentStatus.COMPLETED,
    )
    if filters.document_ids:
        query = query.where(Document.id.in_(filters.document_ids))
    if filters.extensions:
        query = query.where(Document.extension.in_([e.lower() for e in filters.extensions]))
    if filters.uploaded_after is not None:
        query = query.where(Document.created_at >= filters.uploaded_after)
    if filters.uploaded_before is not None:
        query = query.where(Document.created_at < filters.uploaded_before)
    return list(await db.scalars(query))


async def retrieve(
    db: AsyncSession,
    embedder: EmbeddingProvider,
    *,
    user_id: uuid.UUID,
    knowledge_base_ids: Sequence[uuid.UUID],
    query: str,
    candidates: int,
    limit: int,
    similarity_threshold: float,
    mode: SearchMode = SearchMode.HYBRID,
    filters: RetrievalFilters | None = None,
    dedup_threshold: float | None = None,
    fusion: FusionMethod = FusionMethod.RRF,
    alpha: float = 0.5,
) -> RetrievalResult:
    """Return up to `limit` chunks relevant to `query`.

    `candidates` is how many rows each retriever contributes before fusion (TOP_K).
    A chunk is kept if its cosine similarity reaches `similarity_threshold`, or if it
    matched the keyword search (exact terms such as error codes or names can be
    relevant even when the embedding similarity is modest). Keyword-only mode
    applies no threshold.

    `filters` restrict the search to matching documents (by ID, type or upload date) inside
    both retrievers, before ranking. Near-duplicates (`dedup_threshold`) are removed before
    the top `limit` are taken, so a duplicate never displaces a distinct passage.

    Callers must have verified that the user owns `knowledge_base_ids`; the user_id
    filter is applied again here as defence in depth.
    """
    started = time.perf_counter()
    query = normalize_query(query)
    result = RetrievalResult(query=query, mode=mode, chunks=[])
    if not query or not knowledge_base_ids:
        return result

    document_ids: list[uuid.UUID] | None = None
    if filters is not None and filters.active:
        stage = time.perf_counter()
        document_ids = await _filtered_documents(db, user_id, knowledge_base_ids, filters)
        result.filter_documents = len(document_ids)
        result.timings_ms["metadata_filter"] = _ms(stage)
        if not document_ids:
            result.timings_ms["total"] = _ms(started)
            return result

    query_vector: list[float] | None = None
    vector_hits: list[tuple[uuid.UUID, float]] = []
    keyword_hits: list[tuple[uuid.UUID, float]] = []

    if mode is not SearchMode.KEYWORD:
        stage = time.perf_counter()
        query_vector, result.embedding_cached = await embed_query_cached(embedder, query)
        result.timings_ms["embedding"] = _ms(stage)

        stage = time.perf_counter()
        vector_hits = await _vector_search(
            db, user_id, knowledge_base_ids, query_vector, candidates, document_ids
        )
        result.timings_ms["vector_search"] = _ms(stage)

    if mode is not SearchMode.VECTOR:
        stage = time.perf_counter()
        keyword_hits = await _keyword_search(db, user_id, knowledge_base_ids, query, candidates, document_ids)
        result.timings_ms["keyword_search"] = _ms(stage)

    result.vector_candidates, result.keyword_candidates = len(vector_hits), len(keyword_hits)

    stage = time.perf_counter()
    vector_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(vector_hits, start=1)}
    keyword_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(keyword_hits, start=1)}
    keyword_score = dict(keyword_hits)
    if mode is SearchMode.HYBRID and fusion is FusionMethod.WEIGHTED:
        scores = weighted_fusion(vector_hits, keyword_hits, alpha)
    elif mode is SearchMode.HYBRID:
        scores = reciprocal_rank_fusion([[c for c, _ in vector_hits], [c for c, _ in keyword_hits]])
    else:
        scores = dict(vector_hits or keyword_hits)
    result.timings_ms["fusion"] = _ms(stage)

    if scores:
        stage = time.perf_counter()
        similarity_column = (
            (1 - DocumentChunk.embedding.cosine_distance(query_vector)).label("similarity")
            if query_vector is not None
            else None
        )
        columns = [DocumentChunk, Document.filename]
        if similarity_column is not None:
            columns.append(similarity_column)
        rows = await db.execute(
            select(*columns)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.id.in_(list(scores)), DocumentChunk.user_id == user_id)
        )
        chunks: list[RetrievedChunk] = []
        for row in rows:
            chunk: DocumentChunk = row[0]
            similarity = float(row.similarity) if similarity_column is not None else None
            matched_keywords = chunk.id in keyword_rank
            if similarity is not None and similarity < similarity_threshold and not matched_keywords:
                result.filtered_out += 1
                continue
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    knowledge_base_id=chunk.knowledge_base_id,
                    filename=row.filename,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    content=chunk.content,
                    score=scores[chunk.id],
                    similarity=similarity,
                    keyword_score=keyword_score.get(chunk.id),
                    vector_rank=vector_rank.get(chunk.id),
                    keyword_rank=keyword_rank.get(chunk.id),
                )
            )
        # Deterministic order: best score first, ties broken by document position.
        chunks.sort(key=lambda c: (-c.score, str(c.document_id), c.chunk_index))
        result.timings_ms["fetch"] = _ms(stage)
        stage = time.perf_counter()
        chunks, result.duplicates_removed = deduplicate(chunks, dedup_threshold)
        result.timings_ms["dedup"] = _ms(stage)
        result.chunks = chunks[:limit]

    result.timings_ms["total"] = _ms(started)
    logger.info(
        "retrieval_completed",
        extra={
            "mode": mode.value,
            "fusion": fusion.value if mode is SearchMode.HYBRID else None,
            "query_chars": len(query),  # the query text itself is not logged (may be sensitive)
            "knowledge_bases": len(knowledge_base_ids),
            "vector_candidates": result.vector_candidates,
            "keyword_candidates": result.keyword_candidates,
            "filtered_out": result.filtered_out,
            "duplicates_removed": result.duplicates_removed,
            "filter_documents": result.filter_documents,
            "returned": len(result.chunks),
            "top_similarity": max((c.similarity or 0.0 for c in result.chunks), default=None),
            **{f"{name}_ms": value for name, value in result.timings_ms.items()},
        },
    )
    return result
