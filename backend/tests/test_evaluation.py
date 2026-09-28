import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic
import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.evaluation.dataset import EvalDataset, EvalQuestion, Evidence, load_dataset
from app.evaluation.judge import TOOL_NAME, ClaimVerdict, ClaudeJudge, JudgeError, JudgeVerdict
from app.evaluation.metrics import (
    CitedSource,
    Passage,
    covers,
    fact_recall,
    mean,
    normalize,
    percentile,
    score_citations,
    score_ranking,
    states_fact,
)
from app.evaluation.report import render_markdown
from app.evaluation.runner import (
    RETRIEVAL_CONFIGS,
    check_labels,
    evaluate_answers,
    evaluate_retrieval,
    prepare_corpus,
)
from app.rag.extraction import extract_text
from app.rag.reranking import PassthroughReranker
from app.services.storage import LocalFileStorage
from tests.fakes import HashingEmbeddingProvider, ScriptedLLM

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "evaluation" / "dataset.json"

# --- metrics ------------------------------------------------------------------------------------


def test_normalize_folds_case_whitespace_and_curly_quotes() -> None:
    curly_apostrophe, non_breaking_space = chr(0x2019), chr(0xA0)
    text = f"A doctor{curly_apostrophe}s  Note{non_breaking_space}\n here"
    assert normalize(text) == "a doctor's note here"


def test_evidence_must_come_from_the_named_document() -> None:
    evidence = Evidence(document="a.md", evidence="20 days of paid leave")

    assert covers(Passage("a.md", "Staff get 20 DAYS of paid\nleave."), evidence)
    assert not covers(Passage("b.md", "Staff get 20 days of paid leave."), evidence)


def test_ranking_scores() -> None:
    evidence = [Evidence(document="a.md", evidence="alpha"), Evidence(document="a.md", evidence="beta")]
    ranked = [
        Passage("a.md", "nothing"),
        Passage("a.md", "alpha here"),
        Passage("a.md", "alpha again"),  # a duplicate adds no new evidence
        Passage("a.md", "beta here"),
    ]

    scores = score_ranking(ranked, evidence, [1, 3, 5])

    assert scores.hit == {1: 0.0, 3: 1.0, 5: 1.0}
    assert scores.recall == {1: 0.0, 3: 0.5, 5: 1.0}
    assert scores.mrr == 0.5
    assert scores.first_relevant_rank == 2
    assert scores.relevant_ranks == [2, 3, 4]
    # DCG at 5 = 1/log2(3) + 1/log2(5); ideal = 1/log2(2) + 1/log2(3)
    assert scores.ndcg[5] == pytest.approx((0.6309 + 0.4307) / (1 + 0.6309), abs=1e-3)
    assert 0 < scores.ndcg[3] < scores.ndcg[5] <= 1


def test_a_perfect_ranking_scores_one() -> None:
    evidence = [Evidence(document="a.md", evidence="alpha")]
    scores = score_ranking([Passage("a.md", "alpha")], evidence, [1, 5])
    assert scores.hit[1] == scores.recall[5] == scores.ndcg[5] == scores.mrr == 1.0


@pytest.mark.parametrize(
    ("answer", "variants", "expected"),
    [
        ("You get 5 days.", ["5"], True),
        ("You get 15 days.", ["5"], False),
        ("It costs $25.", ["5"], False),
        ("Rate is 5.5%.", ["5"], False),
        ("Up to 5, then more.", ["5"], True),
        ("The limit is $1,500 a year.", ["1,500", "1500"], True),
        ("Capped at $1500.", ["1,500", "1500"], True),
        ("Report it anonymously.", ["anonym"], True),
        ("Twenty days", ["20", "twenty"], True),
        ("Core hours are 10:00 to 16:00.", ["10:00"], True),
        ("Alcohol is not reimbursable.", ["not reimbursable", "cannot"], True),
    ],
)
def test_states_fact(answer: str, variants: list[str], expected: bool) -> None:
    assert states_fact(answer, variants) is expected


def test_fact_recall() -> None:
    assert fact_recall(
        "Business class, and the cap is $275.", [["business"], ["275"], ["vp", "vice president"]]
    ) == (pytest.approx(2 / 3))
    assert fact_recall("anything", []) is None


def test_citation_scores() -> None:
    evidence = [Evidence(document="a.md", evidence="alpha"), Evidence(document="a.md", evidence="beta")]
    sources = [
        CitedSource(Passage("a.md", "alpha text"), quotes_total=2, quotes_verified=2),
        CitedSource(Passage("b.md", "off topic"), quotes_total=1, quotes_verified=0),
    ]

    scores = score_citations(sources, evidence)

    assert scores.cited is True
    assert scores.quote_verification == pytest.approx(2 / 3)
    assert scores.cited_source_relevance == 0.5
    assert scores.evidence_citation_recall == 0.5
    assert score_citations([], evidence).evidence_citation_recall == 0.0


def test_aggregation_helpers() -> None:
    assert mean([1.0, None, 0.0]) == 0.5
    assert mean([None]) is None
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile([1, 2, 3, 4, 100], 95) == 100
    assert percentile([], 95) is None


# --- the dataset ----------------------------------------------------------------------------------


def question(**overrides) -> dict:
    return {
        "id": "x",
        "category": "factual",
        "question": "What?",
        "expected_answer": "That.",
        "expected_facts": [["that"]],
        "relevant": [{"document": "a.md", "evidence": "that"}],
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"category": "unanswerable"}, "unanswerable questions have no relevant evidence"),
        ({"relevant": []}, "answerable questions need relevant evidence"),
        ({"expected_facts": [[]]}, "at least one non-empty phrasing"),
    ],
)
def test_inconsistent_questions_are_rejected(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        EvalQuestion.model_validate(question(**overrides))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate question ids: x"):
        EvalDataset.model_validate(
            {"name": "d", "version": 1, "corpus": ".", "questions": [question(), question()]}
        )


def test_the_committed_dataset_is_valid_and_matches_its_corpus() -> None:
    """Every evidence snippet must appear in the extracted text of its document, so editing
    the corpus can't silently break the labels."""
    dataset, corpus = load_dataset(DATASET)
    texts = {
        path.name: normalize(" ".join(block.text for block in extract_text(path, path.suffix).blocks))
        for path in corpus.iterdir()
        if path.is_file()
    }

    missing = [
        (q.id, e.evidence)
        for q in dataset.questions
        for e in q.relevant
        if normalize(e.evidence) not in texts[e.document]
    ]
    assert missing == []
    assert {q.category for q in dataset.questions} == {
        "factual",
        "paraphrase",
        "keyword",
        "multi_hop",
        "unanswerable",
    }
    assert sum(not q.answerable for q in dataset.questions) >= 5


# --- the judge ----------------------------------------------------------------------------------


def judge_message(tool_input: dict | None) -> dict:
    content = (
        [{"type": "tool_use", "id": "toolu_1", "name": TOOL_NAME, "input": tool_input}]
        if tool_input is not None
        else [{"type": "text", "text": "I think it is fine."}]
    )
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": "tool_use" if tool_input is not None else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 120},
    }


def make_judge(handler) -> tuple[ClaudeJudge, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def recording(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recording)),
    )
    return ClaudeJudge(client, "claude-opus-5"), requests


async def test_judge_forces_the_grading_tool_and_scores_the_verdict() -> None:
    verdict_input = {
        "claims": [
            {"claim": "Employees get 20 days of leave.", "supported": True, "source_numbers": [1]},
            {"claim": "Leave can be sold back.", "supported": False, "source_numbers": []},
        ],
        "answer_relevance": 4,
        "relevance_reason": "Answers the question with an extra claim.",
    }
    judge, requests = make_judge(lambda r: httpx2.Response(200, json=judge_message(verdict_input)))

    verdict = await judge.grade(
        "How much leave?", ["Employees get 20 days of leave."], "20 days; it can be sold back."
    )

    body = json.loads(requests[0].content)
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert body["tools"][0]["name"] == TOOL_NAME
    prompt = body["messages"][0]["content"]
    assert '<passage number="1">\nEmployees get 20 days of leave.\n</passage>' in prompt
    assert "<answer>\n20 days; it can be sold back.\n</answer>" in prompt
    assert verdict.faithfulness == 0.5
    assert verdict.answer_relevance_score == 0.75
    assert verdict.input_tokens == 900


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx2.Response(200, json=judge_message(None)), "did not call record_grading"),
        (
            httpx2.Response(
                200, json=judge_message({"claims": [], "answer_relevance": 9, "relevance_reason": ""})
            ),
            "out of range",
        ),
        (httpx2.Response(200, json=judge_message({"answer_relevance": 3})), "malformed verdict"),
        (
            httpx2.Response(
                529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
            ),
            "judge request failed",
        ),
    ],
    ids=["no-tool-call", "bad-rating", "missing-claims", "api-error"],
)
async def test_judge_failures_are_judge_errors(response: httpx2.Response, message: str) -> None:
    judge, _ = make_judge(lambda r: response)

    with pytest.raises(JudgeError, match=message):
        await judge.grade("q", ["s"], "a")


# --- the runner (test database, fake models) ------------------------------------------------------


class ScriptedJudge:
    model = "test-judge"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str]] = []

    async def grade(self, question: str, sources: list[str], answer: str) -> JudgeVerdict:
        self.calls.append((question, list(sources), answer))
        return JudgeVerdict([ClaimVerdict("c", True, [1])], 5, "fine", "test-judge", 10, 5)


@pytest.fixture
def small_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "leave.md").write_text("# Leave\n\nEmployees receive 20 days of paid annual leave each year.\n")
    (corpus / "security.md").write_text("# Security\n\nPasswords must be at least 14 characters long.\n")
    return corpus


def small_dataset(evidence: str = "20 days of paid annual leave") -> EvalDataset:
    return EvalDataset.model_validate(
        {
            "name": "small",
            "version": 1,
            "corpus": "corpus",
            "questions": [
                question(
                    id="a1",
                    question="How many days of paid annual leave?",
                    expected_facts=[["20"]],
                    relevant=[{"document": "leave.md", "evidence": evidence}],
                ),
                question(
                    id="a2",
                    category="keyword",
                    question="Password characters minimum",
                    expected_facts=[["14"]],
                    relevant=[{"document": "security.md", "evidence": "at least 14 characters"}],
                ),
                question(
                    id="u1",
                    category="unanswerable",
                    question="Who is the CEO?",
                    expected_facts=[],
                    relevant=[],
                ),
            ],
        }
    )


@pytest.mark.integration
async def test_runner_end_to_end(db: AsyncSession, small_corpus: Path, tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    embedder = HashingEmbeddingProvider()

    prepared = await prepare_corpus(SessionLocal, storage, small_corpus, embedding_model=embedder.model_name)
    again = await prepare_corpus(SessionLocal, storage, small_corpus, embedding_model=embedder.model_name)

    assert (prepared.documents, prepared.chunks, prepared.reused) == (2, 2, False)
    assert again.reused and again.knowledge_base_id == prepared.knowledge_base_id

    dataset = small_dataset()
    assert await check_labels(SessionLocal, prepared.knowledge_base_id, dataset) == []
    assert await check_labels(SessionLocal, prepared.knowledge_base_id, small_dataset("21 days")) == [
        "a1: evidence not found in any chunk of leave.md: '21 days'"
    ]

    retrieval = await evaluate_retrieval(
        SessionLocal,
        embedder,
        PassthroughReranker(),
        user_id=prepared.user_id,
        kb_id=prepared.knowledge_base_id,
        questions=dataset.questions,
        config=RETRIEVAL_CONFIGS["keyword"],
        ks=[1, 3],
    )
    assert retrieval["overall"]["questions"] == 2
    assert retrieval["overall"]["hit"][1] == 1.0
    assert retrieval["questions"][0]["results"][0]["relevant"] is True
    assert retrieval["unanswerable"]["empty_retrieval_rate"] == 1.0  # no keyword overlap with "CEO"

    llm = ScriptedLLM()
    llm.answer = "Employees receive 20 days of paid annual leave."
    llm.cite = [(0, "Employees receive 20 days of paid annual leave each year.")]
    judge = ScriptedJudge()
    answers = await evaluate_answers(
        SessionLocal,
        embedder,
        PassthroughReranker(),
        llm,
        judge,
        user_id=prepared.user_id,
        kb_id=prepared.knowledge_base_id,
        questions=dataset.questions[:1],
    )
    detail = answers["details"][0]
    assert detail["fact_recall"] == 1.0
    assert detail["quote_verification"] == 1.0
    assert detail["cited_source_relevance"] == 1.0
    assert detail["faithfulness"] == 1.0 and detail["answer_relevance"] == 1.0
    assert judge.calls[0][2] == llm.answer
    assert answers["answerable"]["fact_recall"] == 1.0

    report = render_markdown(
        {"meta": _meta(prepared), "retrieval": {"keyword": retrieval}, "answers": answers}
    )
    assert "| **keyword** | 100.0% |" in report
    assert "| Fact recall | 100.0% |" in report


def _meta(prepared) -> dict:
    return {
        "dataset": "small",
        "dataset_version": 1,
        "started_at": "now",
        "duration_seconds": 1,
        "questions": {"total": 3, "answerable": 2, "unanswerable": 1},
        "corpus": {
            "documents": prepared.documents,
            "chunks": prepared.chunks,
            "fingerprint": prepared.fingerprint,
        },
        "settings": {"model": "test-llm"},
        "judge_model": "test-judge",
    }


@pytest.mark.integration
async def test_unanswerable_questions_are_scored_on_abstention(
    db: AsyncSession, small_corpus: Path, tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    embedder = HashingEmbeddingProvider()
    prepared = await prepare_corpus(SessionLocal, storage, small_corpus, embedding_model=embedder.model_name)
    llm = ScriptedLLM()
    llm.answer = "The documents don't say who the CEO is."  # no citations -> "not found"
    judge = ScriptedJudge()

    answers = await evaluate_answers(
        SessionLocal,
        embedder,
        PassthroughReranker(),
        llm,
        judge,
        user_id=prepared.user_id,
        kb_id=prepared.knowledge_base_id,
        questions=small_dataset().questions[2:],
    )

    assert answers["unanswerable"]["abstention_rate"] == 1.0
    assert answers["details"][0]["abstention_correct"] is True
    assert judge.calls == []  # nothing to judge in an abstention


# --- the CLI --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "message"),
    [(["--answers"], "--answers needs LLM_API_KEY"), (["--configs", "bogus"], "Unknown configuration")],
)
def test_cli_rejects_bad_options_before_touching_the_database(args: list[str], message: str) -> None:
    env = {
        **os.environ,
        "LLM_API_KEY": "",
        "EVAL_DATABASE_URL": "postgresql+asyncpg://nobody@localhost:1/none",
    }
    result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        [sys.executable, str(REPO / "evaluation" / "evaluate.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 2
    assert message in result.stderr
