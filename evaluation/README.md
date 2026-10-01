# RAG evaluation

Measures how well the assistant finds and uses the right information, on a labelled question set over a small policy corpus. It runs the application's own code, the same ingestion, retrieval, reranking and `answer_question()` that serve `/rag/answer`, so the numbers describe what users get.

```
evaluation/
  dataset.json           36 labelled questions (5 categories, 6 unanswerable)
  corpus/                7 documents in all supported formats (PDF, DOCX, Markdown, text)
  evaluate.py            the command-line runner
  scripts/build_corpus.py  regenerates the PDF and DOCX from reviewable text
  results/               committed baseline reports; per-run output in results/runs/ (gitignored)
backend/app/evaluation/  dataset model, metrics, LLM judge, runner, report (unit-tested)
```

## Running it

From the repository root, with the backend virtualenv active and PostgreSQL running (`docker compose up -d postgres`):

```bash
python evaluation/evaluate.py                         # retrieval metrics for 4 configurations
python evaluation/evaluate.py --answers               # + answer, citation and judge metrics (needs LLM_API_KEY)
python evaluation/evaluate.py --answers --judge none  # answers without the LLM judge (no judge cost)
python evaluation/evaluate.py --configs hybrid+rerank --category paraphrase
python evaluation/evaluate.py --chunk-size 400 --chunk-overlap 60 --top-k 30 --similarity-threshold 0.45
```

- **Isolated.** Everything goes into a separate database, `rag_assistant_eval` (created and migrated automatically; override with `EVAL_DATABASE_URL`), under a dedicated evaluation user. Your data is never read or changed. No API server, worker or Redis is needed.
- **Configurable.** Retrieval depth, reranking, threshold, chunking and the answering model can be set per run (`--help` lists them). They are applied as environment settings, exactly as a deployment would configure them. Changing chunking or the embedding model re-ingests the corpus. Otherwise the ingested corpus is reused, fingerprinted by file contents and ingestion settings.
- **Honest latency.** The query-embedding cache is disabled during evaluation, so latencies include real embedding time.
- **Label check.** Before scoring, every evidence snippet must be found in the ingested chunks. If one isn't, the run stops with exit code 1, because a "miss" caused by a bad label would otherwise look like a retrieval failure. A unit test also checks the committed dataset against the corpus text.
- **Output.** `results/runs/<timestamp>-<dataset>.json` holds every question, every retrieved chunk (rank, score, relevance), every answer and judge verdict. A Markdown summary sits next to it and is printed to the console.

## The dataset

Each question has a category, an expected answer, **expected facts** (each a list of accepted phrasings) and **relevance labels**:

```json
{
  "id": "q27", "category": "multi_hop",
  "question": "I have an 8-hour flight to London. Which class can I book and what is my hotel limit?",
  "expected_facts": [["business"], ["275"], ["vice president", "vp"]],
  "relevant": [
    {"document": "expense_policy.md", "evidence": "Business class is allowed for flights of 6 hours or more"},
    {"document": "expense_policy.md", "evidence": "the cap is $275 per night"}
  ]
}
```

Relevance is labelled with **evidence snippets, not chunk IDs**. A retrieved chunk is relevant when it comes from the named document and contains the evidence text (case- and whitespace-insensitive). Chunk IDs change whenever chunking or extraction changes, but these labels don't, so one dataset evaluates every configuration.

| Category | Questions | What it tests |
|---|---|---|
| factual | 16 | Direct questions, including the spec's example "What is the leave policy?" |
| paraphrase | 7 | Different wording from the document ("vacation" for "annual leave", "glass of wine" for "alcohol") |
| keyword | 3 | Exact terms and codes: "Keeper", "EthicsLine", "SEV2" |
| multi_hop | 4 | Answers that need two passages, sometimes from two documents |
| unanswerable | 6 | Not in the corpus, but on-topic: "bereavement leave", "stock option vesting", "Who is the CEO?" |

The corpus is a fictional company's policies: 4 documents that hold the answers plus 3 **distractors** with overlapping vocabulary. Examples: desk release at 10:30 versus core hours at 10:00, a $50 gift limit versus a $50 wellness allowance, SEV1/SEV2 postmortems versus incident response times, and a CFO where a question asks about the CEO.

## Metrics

**Retrieval** (answerable questions; no LLM involved):

| Metric | Meaning |
|---|---|
| Hit@k | At least one relevant chunk in the top k |
| Recall@k | Share of the question's evidence items found in the top k (matters for multi-hop) |
| MRR | 1 / rank of the first relevant chunk |
| nDCG@k | Rank-aware quality. A chunk only gains if it adds evidence not already found, so near-duplicate chunks can't inflate it |
| Latency p50/p95 | Retrieval, plus reranking where used, per question |
| Unanswerable | How often retrieval returned nothing, and whether a score threshold could tell answerable from unanswerable questions |

Four configurations are compared: `vector` (pgvector with the similarity threshold), `keyword` (PostgreSQL full text), `hybrid` (both, fused with RRF) and `hybrid+rerank` (the production path: 20 candidates reranked by the cross-encoder down to 5).

**Answers** (`--answers`; production pipeline):

| Spec requirement | Metric | How it is measured |
|---|---|---|
| Retrieval relevance | Hit / Recall / MRR / nDCG above | Labelled evidence |
| Answer relevance | Fact recall | Expected facts stated in the answer. Deterministic: numbers must not be part of longer numbers, so "5" doesn't match "15" |
| | Answer relevance | LLM judge rates 1-5 whether the answer addresses the question |
| Faithfulness | Faithfulness | LLM judge splits the answer into atomic claims and checks each against **the passages the model was given**, not its own knowledge |
| Citation correctness | Quotes verified | Cited quotes found verbatim in the cited source (checked by the app, not the judge) |
| | Cited sources relevant | Cited passages that contain labelled evidence |
| | Evidence cited | Labelled evidence covered by the citations |
| (hallucination) | Correct abstention | Unanswerable questions get the "not found" answer, while answerable ones are answered |

The judge ([judge.py](../backend/app/evaluation/judge.py)) is Claude, forced to call a grading tool so its verdict is schema-shaped JSON rather than free text. LLM judges tend to be lenient and to favour longer answers, so judge scores are reported alongside the deterministic checks, not instead of them.

## Results

### Answers: measured

[results/experiment-not-found-lead.md](results/experiment-not-found-lead.md). Production pipeline (hybrid + rerank), `claude-opus-5` answering and judging, 36 questions:

| Metric | Result |
|---|---|
| Fact recall (expected facts in the answer) | 100% |
| Faithfulness (claims supported by the retrieved passages, LLM judge) | 100% |
| Quotes verified (found word for word in the cited source) | 100% |
| Evidence cited | 100% |
| Correct abstention (unanswerable questions labelled "not found") | 100% (was 66.7%) |
| Latency p50 | 5.4 s |

The one change this run led to: answers to unanswerable questions now open with a fixed sentence, so the app labels them "not found" even when they cite related information (66.7% → 100% correct abstention, no answerable question affected).

### Retrieval: measured

[results/baseline-retrieval.md](results/baseline-retrieval.md). Local `BAAI/bge-small-en-v1.5` embeddings, `ms-marco-MiniLM-L-6-v2` reranker, chunk size 1000/150, CPU. 30 answerable questions:

| Configuration | Hit@1 | Hit@3 | Recall@3 | MRR | nDCG@5 | p50 latency |
|---|---|---|---|---|---|---|
| vector | 86.7% | 96.7% | 96.7% | 0.917 | 0.917 | 16 ms |
| keyword | 86.7% | 93.3% | 91.7% | 0.900 | 0.892 | 6 ms |
| **hybrid** | 93.3% | 100% | 100% | 0.961 | 0.958 | 16 ms |
| **hybrid+rerank** | 93.3% | 100% | 100% | 0.967 | 0.960 | 580 ms |

What this shows:

1. **Hybrid search earns its place.** Vector and keyword search each fail on different questions:
   - Vector search misses the exact-term question "What is Keeper?".
   - Keyword search misses the paraphrase "glass of wine" (the policy says "alcohol").
   - Keyword search also misses "SEV2": chunks about working and opening hours outrank the only chunks containing "sev2", because they match the common word "time". PostgreSQL's `ts_rank_cd` has no IDF, so it doesn't know that "sev2" is rare and decisive (see experiment 5).

   Fused, they find the evidence for every question in the top 3.
2. **The reranker adds almost nothing on this corpus, for about 560 ms.** Hybrid retrieval already saturates a 37-chunk corpus (MRR 0.961 → 0.967). The reranker is kept in production because larger, noisier knowledge bases are where it pays off. This corpus is too small to demonstrate that, so it remains a hypothesis, and `RERANKER_PROVIDER=none` removes the cost.
3. **No score threshold can detect unanswerable questions.** In-domain unanswerable questions score 0.62–0.74 top-1 similarity, overlapping answerable ones, which start at 0.57. Similarity measures topic, not whether the answer is present. The cross-encoder separates better (3 of 6 unanswerable questions score about −10), but "bereavement leave" scores +4.0 by matching the leave section, while the answerable "glass of wine" paraphrase scores −9.9. Retrieval returned passages for every unanswerable question. So abstaining is Claude's job, which is what the grounded prompt instructs and why "correct abstention" is an answer metric.
4. **Chunk size.** With 400/60 instead of 1000/150 ([results/experiment-chunk-size-400.md](results/experiment-chunk-size-400.md)), Hit@1 rose from 93.3% to 96.7% and reranking got cheaper (364 ms). That's one question out of 30, which is within the noise of a dataset this size, so the default was not changed.

5. **An intuitive fix, rejected by measurement.** To address the missing-IDF problem, keyword ranking was changed to weight each matched term by its BM25 IDF over the user's chunks. That fixed the SEV2 question, but made results worse overall ([results/experiment-idf-keyword-ranking.md](results/experiment-idf-keyword-ranking.md)):

   | | keyword MRR | hybrid MRR | hybrid Hit@1 |
   |---|---|---|---|
   | `ts_rank_cd` (kept) | 0.900 | 0.961 | 93.3% |
   | IDF-weighted | 0.875 | 0.939 | 90.0% |

   Without term frequency and length normalisation, rare but incidental words dominated, and "Am I allowed to work from home?" became a new miss. The change was reverted rather than tuned against 30 questions, which would overfit. Hybrid fusion already recovers the SEV2 case: with vector search in the mix, the right chunk ranks second.

6. **A relevance threshold after reranking costs recall.** Withholding passages the cross-encoder scores below a cut-off ([results/experiment-rerank-threshold.md](results/experiment-rerank-threshold.md)) sends less context, but every value tried also withheld relevant passages: -10 sends about 40% fewer passages and drops recall@5 from 100% to 96.7%; -8 makes half the unanswerable questions retrieve nothing but leaves one answerable question with no evidence at all. `RERANK_MIN_SCORE` stays off by default. Near-duplicate removal (`DEDUP_THRESHOLD=0.9`, on by default) left every metric unchanged here, because this corpus has no duplicate passages.
7. **RRF beats weighted score fusion.** A weighted blend of min-max normalised similarity and keyword scores ([results/experiment-fusion.md](results/experiment-fusion.md)) was as good as RRF at best: alpha 0.5 matches Hit@1 (93.3%) but drops recall@3 to 96.7%, and every weight tried helps one question and hurts another. Normalisation stretches small keyword-score gaps, so chunks matching only a common word ("time") nearly tie the one containing "SEV2". RRF stays the default; with reranking the choice made no difference here.

**Limits of these numbers.** With 30 answerable questions, one question moves a rate by 3.3 points. The corpus is small (37 chunks) and was written for this evaluation, so these figures show how the configurations compare and catch regressions. They are not an estimate of accuracy on real document collections. Add questions over your own documents to `dataset.json` for that.

### Answers, faithfulness and citations: not yet measured

These metrics need Claude. No `LLM_API_KEY` was available during development, so **no answer-quality numbers are reported**. The whole path was checked end to end against a local mock of the Anthropic API: the real SDK, the pipeline, the judge's tool call, the scoring and the report, with 36 questions, 0 errors and 0 judge errors. The mock's scores are meaningless and are not published. With a key, `python evaluation/evaluate.py --answers` produces the answer table in the report.

A cost note: with the judge, each question makes two Claude calls, the answer (about 1–2k input tokens with 5 passages) and the grading. Use `--judge none` or `--ids` / `--category` to run a subset.
