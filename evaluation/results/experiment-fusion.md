# Experiment: reciprocal rank fusion vs weighted score fusion

**Question.** Hybrid search merges the vector and keyword result lists. Reciprocal rank fusion
(RRF) uses only each chunk's rank in each list. The common alternative is a weighted sum of the
two scores, `alpha * similarity + (1 - alpha) * keyword score`, after min-max normalising each list
(cosine similarity and `ts_rank_cd` are on unrelated scales). Is either better here, and for
which `alpha`?

**Setup.** Same corpus and dataset as the [baseline](baseline-retrieval.md) (7 documents,
37 chunks, 30 answerable questions), deduplication on, local models. One run per row:
`python evaluation/evaluate.py --configs hybrid,hybrid+rerank --fusion=<rrf|weighted> --hybrid-alpha=<alpha>`
(2026-10-01). `alpha` weights semantic similarity.

| Fusion | Configuration | Hit@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | Recall@3 on keyword questions |
|---|---|---|---|---|---|---|---|
| **RRF (default)** | hybrid | **93.3%** | **100.0%** | 100.0% | **0.961** | **0.958** | 100% |
| weighted, alpha 0.3 | hybrid | 86.7% | 96.7% | 100.0% | 0.919 | 0.927 | 67% |
| weighted, alpha 0.5 | hybrid | 93.3% | 96.7% | 100.0% | 0.958 | 0.956 | 67% |
| weighted, alpha 0.7 | hybrid | 90.0% | 100.0% | 100.0% | 0.944 | 0.946 | 100% |
| any of the above | hybrid+rerank | 93.3% | 100.0% | 100.0% | 0.967 | 0.960 | 100% |

Rank of the first relevant chunk where the methods differ (hybrid, no reranking):

| Question | RRF | alpha 0.3 | alpha 0.5 | alpha 0.7 |
|---|---|---|---|---|
| q02 What is the leave policy? | 1 | 2 | 1 | 1 |
| q22 Will the company pay for my gym membership? | 1 | 2 | 1 | 1 |
| q23 Can I claim a glass of wine at dinner on a business trip? | 3 | 3 | 2 | 2 |
| q24 What is Keeper? | 1 | 1 | 1 | 2 |
| q26 What is the response time for a SEV2? | 2 | 4 | 4 | 3 |

**Findings.**

- **RRF is as good or better on every metric, at every `alpha` tried.** Each weight helps one
  question and hurts others: 0.5 and 0.7 lift the wine paraphrase (q23), but 0.3 and 0.5 push
  the SEV2 question out of the top 3, and 0.7 demotes "What is Keeper?".
- **Why SEV2 suffers.** Min-max normalisation stretches the keyword list's small score gaps to the
  full 0..1 range, so chunks matching only the common word "time" get keyword scores close to the
  one chunk containing "sev2". RRF ignores score gaps and rewards chunks both retrievers rank, which
  is what lifts the right chunk here (see finding 1 of the evaluation README).
- **With reranking the choice doesn't matter on this corpus.** The cross-encoder reorders the
  20 candidates, and every method puts the evidence somewhere in them.

**Decision.** RRF stays the default (`RETRIEVAL_FUSION=rrf`). It has no weight to tune, which also
matters: an `alpha` tuned on 30 questions would overfit them. Weighted fusion remains available
(`RETRIEVAL_FUSION=weighted`, `HYBRID_ALPHA`) and selectable per search in the Search tab's
advanced settings, for comparing the two on your own documents.
