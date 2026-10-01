"""Runs an evaluation through the application's own code paths.

The corpus is ingested with the real pipeline (extraction, chunking, embeddings), searched
with the real `retrieve()` and reranker, and answered with the same `answer_question()`
that serves `/rag/answer`. What is measured is what users get.

Nothing here talks to the HTTP API: it runs in-process against a dedicated database
(see evaluation/evaluate.py), so any configuration can be compared without restarting
servers, and no worker process is needed.
"""

import dataclasses
import hashlib
import io
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import Headers, UploadFile

from app.core.config import get_settings
from app.core.security import hash_password
from app.evaluation.dataset import EvalDataset, EvalQuestion
from app.evaluation.judge import ClaudeJudge, JudgeError
from app.evaluation.metrics import (
    CitedSource,
    Passage,
    covers,
    fact_recall,
    mean,
    percentile,
    score_citations,
    score_ranking,
)
from app.llm.base import LLMError, LLMProvider
from app.models import Document, DocumentChunk, DocumentStatus, KnowledgeBase, User
from app.rag.context import select_context
from app.rag.embeddings import EmbeddingProvider
from app.rag.pipeline import AnswerType, answer_question
from app.rag.reranking import RankedChunk, Reranker, rerank_or_fallback
from app.rag.retrieval import SearchMode, retrieve
from app.services import document_service
from app.services.ingestion_service import process_document
from app.services.storage import LocalFileStorage
from app.utils.files import SUPPORTED_FILE_TYPES

logger = logging.getLogger(__name__)

EVAL_USER_EMAIL = "evaluation@example.com"
Processor = Callable[..., Awaitable[DocumentStatus | None]]


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    name: str
    mode: SearchMode
    rerank: bool
    description: str


RETRIEVAL_CONFIGS: dict[str, RetrievalConfig] = {
    c.name: c
    for c in (
        RetrievalConfig(
            "vector", SearchMode.VECTOR, False, "pgvector cosine search only (with the similarity threshold)"
        ),
        RetrievalConfig("keyword", SearchMode.KEYWORD, False, "PostgreSQL full-text search only"),
        RetrievalConfig("hybrid", SearchMode.HYBRID, False, "vector + keyword fused with RRF"),
        RetrievalConfig(
            "hybrid+rerank",
            SearchMode.HYBRID,
            True,
            "hybrid, then the cross-encoder picks the top RERANK_TOP_K (production)",
        ),
    )
}


@dataclass(slots=True)
class PreparedCorpus:
    user_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    documents: int
    chunks: int
    fingerprint: str
    reused: bool
    ingestion_seconds: float | None


def corpus_fingerprint(corpus: Path, embedding_model: str) -> str:
    """Changes whenever the corpus files or anything affecting ingestion changes."""
    settings = get_settings()
    digest = hashlib.sha256()
    for path in sorted(corpus_files(corpus)):
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(f"{settings.chunk_size}|{settings.chunk_overlap}|{embedding_model}".encode())
    return digest.hexdigest()[:16]


def corpus_files(corpus: Path) -> list[Path]:
    return sorted(p for p in corpus.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_FILE_TYPES)


async def _evaluation_user(db: AsyncSession) -> User:
    user = await db.scalar(select(User).where(User.email == EVAL_USER_EMAIL))
    if user is None:
        # Never used to sign in: a random password nobody knows.
        user = User(
            email=EVAL_USER_EMAIL,
            hashed_password=await hash_password(uuid.uuid4().hex),
            full_name="Evaluation",
        )
        db.add(user)
        await db.commit()
    return user


async def prepare_corpus(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    corpus: Path,
    *,
    embedding_model: str,
    fresh: bool = False,
    processor: Processor = process_document,
) -> PreparedCorpus:
    """Ingest the corpus into a knowledge base named after its fingerprint, or reuse one that
    was fully ingested with the same corpus and settings."""
    fingerprint = corpus_fingerprint(corpus, embedding_model)
    name = f"eval-{fingerprint}"
    files = corpus_files(corpus)
    async with session_factory() as db:
        user = await _evaluation_user(db)
        existing = await db.scalar(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user.id, KnowledgeBase.name == name)
        )
        if existing is not None and not fresh:
            statuses = list(
                await db.scalars(select(Document.status).where(Document.knowledge_base_id == existing.id))
            )
            if len(statuses) == len(files) and all(s is DocumentStatus.COMPLETED for s in statuses):
                chunks = await _chunk_count(db, existing.id)
                return PreparedCorpus(user.id, existing.id, len(files), chunks, fingerprint, True, None)
        if existing is not None:
            await db.delete(existing)  # stale or incomplete: rebuild (files are left for cleanup)
            await db.commit()
        kb = KnowledgeBase(
            user_id=user.id, name=name, description="Evaluation corpus (evaluation/evaluate.py)"
        )
        db.add(kb)
        await db.commit()
        document_ids = []
        for path in files:
            upload = UploadFile(io.BytesIO(path.read_bytes()), filename=path.name, headers=Headers({}))
            document_ids.append((await document_service.upload(db, storage, user.id, kb.id, upload)).id)
        user_id, kb_id = user.id, kb.id

    started = time.perf_counter()
    for document_id in document_ids:
        await processor(document_id, session_factory=session_factory, storage=storage)
    elapsed = round(time.perf_counter() - started, 2)

    async with session_factory() as db:
        failed = list(
            await db.execute(
                select(Document.filename, Document.error_message).where(
                    Document.knowledge_base_id == kb_id, Document.status != DocumentStatus.COMPLETED
                )
            )
        )
        if failed:
            raise RuntimeError(f"corpus ingestion failed: {[tuple(row) for row in failed]}")
        chunks = await _chunk_count(db, kb_id)
    return PreparedCorpus(user_id, kb_id, len(files), chunks, fingerprint, False, elapsed)


async def _chunk_count(db: AsyncSession, kb_id: uuid.UUID) -> int:
    return len(
        list(await db.scalars(select(DocumentChunk.id).where(DocumentChunk.knowledge_base_id == kb_id)))
    )


async def check_labels(
    session_factory: async_sessionmaker[AsyncSession], kb_id: uuid.UUID, dataset: EvalDataset
) -> list[str]:
    """Every evidence snippet must be findable in some chunk of its document; otherwise a
    retrieval miss would be a labelling error, not a system error."""
    async with session_factory() as db:
        rows = await db.execute(
            select(Document.filename, DocumentChunk.content)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.knowledge_base_id == kb_id)
        )
        passages = [Passage(filename, content) for filename, content in rows]
    problems = []
    for question in dataset.questions:
        for item in question.relevant:
            if not any(covers(p, item) for p in passages):
                problems.append(
                    f"{question.id}: evidence not found in any chunk of {item.document}: {item.evidence!r}"
                )
    return problems


def _passage(ranked: RankedChunk) -> Passage:
    return Passage(ranked.chunk.filename, ranked.chunk.content)


def _summary_row(question: EvalQuestion, retrieved: Sequence[RankedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "filename": r.chunk.filename,
            "page": r.chunk.page_number,
            "section": r.chunk.section,
            "similarity": r.chunk.similarity,
            "rerank_score": r.rerank_score,
            "relevant": any(covers(_passage(r), e) for e in question.relevant),
            "preview": r.chunk.content[:120],
        }
        for rank, r in enumerate(retrieved, start=1)
    ]


async def evaluate_retrieval(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: EmbeddingProvider,
    reranker: Reranker,
    *,
    user_id: uuid.UUID,
    kb_id: uuid.UUID,
    questions: Sequence[EvalQuestion],
    config: RetrievalConfig,
    ks: Sequence[int],
) -> dict[str, Any]:
    settings = get_settings()
    depth = max(ks)
    per_question, latencies = [], []
    for question in questions:
        started = time.perf_counter()
        async with session_factory() as db:
            result = await retrieve(
                db,
                embedder,
                user_id=user_id,
                knowledge_base_ids=[kb_id],
                query=question.question,
                candidates=settings.top_k,
                # Reranking sees RERANK_CANDIDATES chunks and keeps RERANK_TOP_K, as in production.
                limit=settings.rerank_candidates if config.rerank else depth,
                similarity_threshold=settings.similarity_threshold,
                mode=config.mode,
                dedup_threshold=settings.dedup_threshold,
            )
        if config.rerank:
            # Exactly the production selection: rerank every candidate, then threshold/top-k/budget.
            candidates, _ = await rerank_or_fallback(
                reranker, question.question, result.chunks, len(result.chunks)
            )
            ranked = select_context(
                candidates,
                top_k=settings.rerank_top_k,
                min_rerank_score=settings.rerank_min_score,
                max_chars=settings.context_max_chars,
            ).passages
        else:
            ranked = [RankedChunk(chunk, None) for chunk in result.chunks[:depth]]
        latencies.append((time.perf_counter() - started) * 1000)

        row: dict[str, Any] = {
            "id": question.id,
            "category": question.category,
            "question": question.question,
            "retrieved": len(ranked),
            "latency_ms": round(latencies[-1], 2),
            "results": _summary_row(question, ranked),
        }
        if question.answerable:
            scores = score_ranking([_passage(r) for r in ranked], question.relevant, ks)
            row.update(
                hit=scores.hit,
                recall=scores.recall,
                ndcg=scores.ndcg,
                mrr=scores.mrr,
                first_relevant_rank=scores.first_relevant_rank,
            )
        per_question.append(row)

    return {
        "config": {"mode": config.mode.value, "rerank": config.rerank, "description": config.description},
        "overall": _aggregate([r for r in per_question if "mrr" in r], ks),
        "by_category": {
            category: _aggregate(rows, ks)
            for category, rows in _group([r for r in per_question if "mrr" in r]).items()
        },
        "unanswerable": _unanswerable_retrieval([r for r in per_question if "mrr" not in r]),
        "answerability_separation": _separation(
            per_question, "rerank_score" if config.rerank else "similarity"
        ),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": percentile(latencies, 100),
        },
        "questions": per_question,
    }


def _group(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["category"]].append(row)
    return dict(groups)


def _aggregate(rows: Sequence[dict[str, Any]], ks: Sequence[int]) -> dict[str, Any]:
    if not rows:
        return {"questions": 0}
    return {
        "questions": len(rows),
        "hit": {k: mean(r["hit"][k] for r in rows) for k in ks},
        "recall": {k: mean(r["recall"][k] for r in rows) for k in ks},
        "ndcg": {k: mean(r["ndcg"][k] for r in rows) for k in ks},
        "mrr": mean(r["mrr"] for r in rows),
    }


def _separation(rows: Sequence[dict[str, Any]], score: str) -> dict[str, Any] | None:
    """Could a threshold on the top result's score tell answerable from unanswerable
    questions? Only if every answerable question scores above every unanswerable one."""
    tops = [
        (row["category"] != "unanswerable", row["results"][0][score])
        for row in rows
        if row["results"] and row["results"][0][score] is not None
    ]
    answerable = [value for is_answerable, value in tops if is_answerable]
    unanswerable = [value for is_answerable, value in tops if not is_answerable]
    if not answerable or not unanswerable:
        return None
    return {
        "score": score,
        "answerable_min": round(min(answerable), 3),
        "unanswerable_max": round(max(unanswerable), 3),
        "separable": min(answerable) > max(unanswerable),
        "unanswerable_above_answerable_min": sum(v >= min(answerable) for v in unanswerable),
    }


def _unanswerable_retrieval(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Nothing is relevant for these; retrieving nothing lets the pipeline answer "not found"
    without calling the LLM. Retrieving something is not an error by itself: the LLM is
    then expected to say the documents don't cover it."""
    if not rows:
        return {"questions": 0}
    return {
        "questions": len(rows),
        "empty_retrieval_rate": mean(1.0 if r["retrieved"] == 0 else 0.0 for r in rows),
        "mean_retrieved": mean(float(r["retrieved"]) for r in rows),
    }


async def evaluate_answers(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: EmbeddingProvider,
    reranker: Reranker,
    llm: LLMProvider,
    judge: ClaudeJudge | None,
    *,
    user_id: uuid.UUID,
    kb_id: uuid.UUID,
    questions: Sequence[EvalQuestion],
) -> dict[str, Any]:
    """Answer every question with the production pipeline, then score the answers."""
    settings = get_settings()
    per_question = []
    for question in questions:
        started = time.perf_counter()
        row: dict[str, Any] = {
            "id": question.id,
            "category": question.category,
            "question": question.question,
        }
        try:
            async with session_factory() as db:
                result = await answer_question(
                    db,
                    user_id=user_id,
                    knowledge_base_ids=[kb_id],
                    question=question.question,
                    embedder=embedder,
                    reranker=reranker,
                    llm=llm,
                    candidates=settings.top_k,
                    rerank_candidates=settings.rerank_candidates,
                    top_k=settings.rerank_top_k,
                    similarity_threshold=settings.similarity_threshold,
                )
        except LLMError as exc:
            row.update(error=f"{exc.code}: {exc.message}")
            per_question.append(row)
            continue

        abstained = result.answer_type is AnswerType.NOT_FOUND
        cited = [
            CitedSource(
                Passage(c.source.chunk.filename, c.source.chunk.content),
                quotes_total=len(c.quotes),
                quotes_verified=sum(q.start is not None for q in c.quotes),
            )
            for c in result.citations
        ]
        citations = score_citations(cited, question.relevant)
        row.update(
            answer=result.answer,
            answer_type=result.answer_type.value,
            abstained=abstained,
            abstention_correct=abstained != question.answerable,
            fact_recall=None if abstained else fact_recall(result.answer, question.expected_facts),
            cited=citations.cited,
            quote_verification=citations.quote_verification,
            cited_source_relevance=citations.cited_source_relevance,
            evidence_citation_recall=citations.evidence_citation_recall,
            sources=[f"{s.chunk.filename}#{s.chunk.chunk_index}" for s in result.sources],
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            timings_ms=result.timings_ms,
        )
        if judge is not None and not abstained:
            try:
                verdict = await judge.grade(
                    question.question, [s.chunk.content for s in result.sources], result.answer
                )
                row.update(
                    faithfulness=verdict.faithfulness,
                    answer_relevance=verdict.answer_relevance_score,
                    judge={
                        "claims": [dataclasses.asdict(c) for c in verdict.claims],
                        "answer_relevance": verdict.answer_relevance,
                        "reason": verdict.relevance_reason,
                        "input_tokens": verdict.input_tokens,
                        "output_tokens": verdict.output_tokens,
                    },
                )
            except JudgeError as exc:
                row.update(judge_error=str(exc))
        per_question.append(row)

    answered = [r for r in per_question if "error" not in r]
    answerable = [r for r in answered if r["category"] != "unanswerable"]
    unanswerable = [r for r in answered if r["category"] == "unanswerable"]
    latencies = [r["latency_ms"] for r in answered]
    return {
        "questions": len(per_question),
        "errors": len(per_question) - len(answered),
        "answerable": {
            "questions": len(answerable),
            "answered_rate": mean(0.0 if r["abstained"] else 1.0 for r in answerable),
            "fact_recall": mean(r["fact_recall"] for r in answerable),
            "cited_rate": mean(1.0 if r["cited"] else 0.0 for r in answerable if not r["abstained"]),
            "quote_verification": mean(r["quote_verification"] for r in answerable),
            "cited_source_relevance": mean(r["cited_source_relevance"] for r in answerable),
            "evidence_citation_recall": mean(
                r["evidence_citation_recall"] for r in answerable if not r["abstained"]
            ),
            "faithfulness": mean(r.get("faithfulness") for r in answerable),
            "answer_relevance": mean(r.get("answer_relevance") for r in answerable),
            "judge_errors": sum(1 for r in answerable if "judge_error" in r),
        },
        "unanswerable": {
            "questions": len(unanswerable),
            "abstention_rate": mean(1.0 if r["abstained"] else 0.0 for r in unanswerable),
            "faithfulness_when_answered": mean(r.get("faithfulness") for r in unanswerable),
        },
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
        "tokens": {
            "input": sum(r.get("input_tokens", 0) for r in answered),
            "output": sum(r.get("output_tokens", 0) for r in answered),
        },
        "details": per_question,
    }
