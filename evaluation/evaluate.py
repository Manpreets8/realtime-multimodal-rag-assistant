"""Evaluate the RAG pipeline on evaluation/dataset.json.

Run from the repository root with the backend virtualenv active (PostgreSQL must be up):

    python evaluation/evaluate.py                       # retrieval metrics, all configurations
    python evaluation/evaluate.py --answers             # + answers, citations, LLM judge (needs LLM_API_KEY)
    python evaluation/evaluate.py --configs hybrid+rerank --top-k 30 --similarity-threshold 0.45

Everything runs in-process through the application's own code (ingestion, retrieval,
reranking, answer_question) against a separate database, rag_assistant_eval, so it never
touches your data and needs no API server or ingestion worker. The corpus is ingested
once and reused until the corpus or the ingestion settings change.

Results: evaluation/results/runs/<timestamp>-<dataset>.json (every question, every
retrieved chunk, every judge verdict) and a Markdown summary next to it.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://rag:rag@localhost:5433/rag_assistant_eval"
ALL_CONFIGS = ["vector", "keyword", "hybrid", "hybrid+rerank"]

# Settings that can be overridden per run; values are written to the environment before
# the application reads its settings, exactly as a deployment would configure them.
SETTING_FLAGS = {
    "top_k": ("TOP_K", int, "candidates per retriever before fusion"),
    "rerank_candidates": ("RERANK_CANDIDATES", int, "chunks passed to the reranker"),
    "rerank_top_k": ("RERANK_TOP_K", int, "chunks kept after reranking (sent to the LLM)"),
    "similarity_threshold": ("SIMILARITY_THRESHOLD", float, "minimum cosine similarity for vector-only hits"),
    "chunk_size": ("CHUNK_SIZE", int, "characters per chunk (re-ingests the corpus)"),
    "chunk_overlap": ("CHUNK_OVERLAP", int, "overlap between chunks (re-ingests the corpus)"),
    "reranker": ("RERANKER_PROVIDER", str, "local | none"),
    "model": ("MODEL_NAME", str, "Claude model that answers"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", type=Path, default=HERE / "dataset.json")
    parser.add_argument(
        "--configs", default=",".join(ALL_CONFIGS), help=f"comma-separated: {', '.join(ALL_CONFIGS)}"
    )
    parser.add_argument("--k", default="1,3,5", help="cut-offs for hit/recall/nDCG (default 1,3,5)")
    parser.add_argument(
        "--answers", action="store_true", help="also generate and score answers (needs LLM_API_KEY)"
    )
    parser.add_argument(
        "--judge", choices=["claude", "none"], default="claude", help="LLM judge for --answers"
    )
    parser.add_argument("--judge-model", help="default: the answering model")
    parser.add_argument("--ids", help="only these question ids (comma-separated)")
    parser.add_argument("--category", help="only this category")
    parser.add_argument("--fresh", action="store_true", help="re-ingest the corpus even if unchanged")
    parser.add_argument("--database-url", default=os.environ.get("EVAL_DATABASE_URL", DEFAULT_DATABASE_URL))
    parser.add_argument("--output-dir", type=Path, default=HERE / "results" / "runs")
    for name, (_, kind, help_text) in SETTING_FLAGS.items():
        parser.add_argument(f"--{name.replace('_', '-')}", type=kind, help=help_text)
    return parser.parse_args()


def configure_environment(args: argparse.Namespace) -> None:
    os.environ["DATABASE_URL"] = args.database_url
    os.environ["UPLOAD_DIR"] = str(HERE / ".data" / "uploads")
    os.environ["QUERY_EMBEDDING_CACHE_TTL_SECONDS"] = "0"  # measure real embedding latency
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ["LOG_JSON"] = "false"
    for name, (env, _, _) in SETTING_FLAGS.items():
        value = getattr(args, name)
        if value is not None:
            os.environ[env] = str(value)
    sys.path.insert(0, str(BACKEND))


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, fixed arguments
            cwd=HERE,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


async def ensure_database(url: str) -> None:
    import asyncpg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import make_url

    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database="postgres",
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", parsed.database):
            await conn.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await conn.close()
    await asyncio.to_thread(command.upgrade, Config(str(BACKEND / "alembic.ini")), "head")


async def run(args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.db.session import SessionLocal, engine
    from app.evaluation.dataset import load_dataset
    from app.evaluation.judge import ClaudeJudge
    from app.evaluation.report import render_markdown
    from app.evaluation.runner import (
        RETRIEVAL_CONFIGS,
        check_labels,
        evaluate_answers,
        evaluate_retrieval,
        prepare_corpus,
    )
    from app.llm import claude
    from app.rag import embeddings, reranking
    from app.services.storage import LocalFileStorage

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    dataset, corpus = load_dataset(args.dataset)
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in configs if c not in RETRIEVAL_CONFIGS]
    ks = sorted({int(k) for k in args.k.split(",")})
    if unknown:
        print(f"Unknown configuration(s): {unknown}. Choose from {ALL_CONFIGS}.", file=sys.stderr)
        return 2
    if args.answers and settings.llm_api_key is None:
        print(
            "--answers needs LLM_API_KEY (an Anthropic API key) in .env or the environment.", file=sys.stderr
        )
        return 2

    questions = dataset.questions
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q.id in wanted]
    if args.category:
        questions = [q for q in questions if q.category == args.category]
    if not questions:
        print("No questions match the filters.", file=sys.stderr)
        return 2

    started_at, started = datetime.now(UTC), time.perf_counter()
    await ensure_database(args.database_url)
    embedder, reranker = embeddings.get_embedding_provider(), reranking.get_reranker()
    storage = LocalFileStorage(settings.upload_dir)
    try:
        prepared = await prepare_corpus(
            SessionLocal, storage, corpus, embedding_model=embedder.model_name, fresh=args.fresh
        )
        how = "reused" if prepared.reused else f"ingested in {prepared.ingestion_seconds} s"
        print(f"Corpus: {prepared.documents} documents, {prepared.chunks} chunks ({how})", file=sys.stderr)

        problems = await check_labels(SessionLocal, prepared.knowledge_base_id, dataset)
        if problems:
            print("Dataset labels don't match the ingested corpus:", *problems, sep="\n  ", file=sys.stderr)
            return 1

        result: dict = {"retrieval": {}, "answers": None}
        for name in configs:
            print(f"Retrieval: {name}", file=sys.stderr)
            result["retrieval"][name] = await evaluate_retrieval(
                SessionLocal,
                embedder,
                reranker,
                user_id=prepared.user_id,
                kb_id=prepared.knowledge_base_id,
                questions=questions,
                config=RETRIEVAL_CONFIGS[name],
                ks=ks,
            )

        judge_model = None
        if args.answers:
            judge = None
            if args.judge == "claude":
                import anthropic

                judge_model = args.judge_model or settings.model_name
                client = anthropic.AsyncAnthropic(
                    api_key=settings.llm_api_key.get_secret_value(), max_retries=3
                )
                judge = ClaudeJudge(client, judge_model)
            print(f"Answers: {len(questions)} questions with {settings.model_name}", file=sys.stderr)
            result["answers"] = await evaluate_answers(
                SessionLocal,
                embedder,
                reranker,
                claude.get_llm_client(),
                judge,
                user_id=prepared.user_id,
                kb_id=prepared.knowledge_base_id,
                questions=questions,
            )
    finally:
        await engine.dispose()

    result["meta"] = {
        "dataset": dataset.name,
        "dataset_version": dataset.version,
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_seconds": round(time.perf_counter() - started, 1),
        "git_commit": git_commit(),
        "questions": {
            "total": len(questions),
            "answerable": sum(q.answerable for q in questions),
            "unanswerable": sum(not q.answerable for q in questions),
        },
        "corpus": {
            "documents": prepared.documents,
            "chunks": prepared.chunks,
            "fingerprint": prepared.fingerprint,
        },
        "settings": {
            "embedding": f"{settings.embedding_provider.value}/{embedder.model_name}",
            "reranker": getattr(reranker, "model_name", "none"),
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "top_k": settings.top_k,
            "rerank_candidates": settings.rerank_candidates,
            "rerank_top_k": settings.rerank_top_k,
            "similarity_threshold": settings.similarity_threshold,
            "model": settings.model_name if args.answers else None,
        },
        "judge_model": judge_model,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{dataset.name}"
    json_path, md_path = args.output_dir / f"{stem}.json", args.output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    markdown = render_markdown(result)
    md_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Details: {json_path}\nReport:  {md_path}", file=sys.stderr)
    return 0


def main() -> int:
    args = parse_args()
    configure_environment(args)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
