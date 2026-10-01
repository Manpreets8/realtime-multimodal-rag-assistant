# Experiment: a relevance threshold on reranker scores

**Question.** After the cross-encoder reranks the candidates, should passages scoring below some
value be withheld from the model, to send less irrelevant context and to let questions the
documents can't answer retrieve nothing?

**Setup.** Same corpus and dataset as the [baseline](baseline-retrieval.md): 7 documents,
37 chunks, 30 answerable and 6 unanswerable questions; `hybrid+rerank`, `RERANK_TOP_K=5`,
deduplication on (0.9), local models only. Each row is one run of
`python evaluation/evaluate.py --configs hybrid+rerank --rerank-min-score=<value>`
(2026-10-01). The selection code is the production `select_context()`.

| RERANK_MIN_SCORE | Hit@1 | Recall@5 | MRR | Answerable: mean passages | Answerable: none left | Unanswerable: mean passages | Unanswerable: none left |
|---|---|---|---|---|---|---|---|
| none (default) | 93.3% | 100.0% | 0.967 | 4.73 | 0 | 5.00 | 0/6 |
| -10 | 93.3% | 96.7% | 0.950 | 2.77 | 0 | 2.83 | 0/6 |
| -8 | 93.3% | 95.0% | 0.950 | 2.03 | 1 | 2.00 | 3/6 |
| -6 | 93.3% | 93.3% | 0.950 | 1.57 | 1 | 2.00 | 3/6 |
| -4 | 80.0% | 80.0% | 0.817 | 1.23 | 5 | 1.50 | 3/6 |
| -2 | 76.7% | 78.3% | 0.783 | 1.07 | 6 | 1.33 | 4/6 |
| 0 | 60.0% | 63.3% | 0.617 | 0.80 | 11 | 0.33 | 5/6 |
| 2 | 53.3% | 56.7% | 0.550 | 0.73 | 13 | 0.33 | 5/6 |

("Answerable: mean passages" counts every passage sent, relevant or not, so the default's 4.73 is
below 5 only where fewer than 5 candidates existed.)

**Findings.**

- **Every threshold loses evidence.** Even -10, which removes about 40% of the passages, drops
  recall@5 from 100% to 96.7%: a relevant passage the reranker scored very low (one answerable
  question's best passage scored -9.9 in the baseline) is withheld.
- **Detecting unanswerable questions costs answerable ones.** At -8, half the unanswerable
  questions correctly retrieve nothing, but one answerable question loses all its evidence and
  would be answered "not found". Thresholds from -4 upwards fail a sixth or more of the answerable
  questions.
- The scores of relevant and irrelevant passages overlap on this corpus, consistent with the
  baseline's finding that the top rerank score can't separate answerable from unanswerable
  questions (answerable min -9.871, unanswerable max 4.001).

**Decision.** `RERANK_MIN_SCORE` stays **off** by default: recall matters more here, and the
grounded prompt already makes the model say the documents don't contain the answer when the
sources don't support one (and the app reports `not_found` when nothing is cited). The setting
remains available as a documented trade-off: around -10 sends about 40% less context for a small
recall cost, which may suit cost-sensitive deployments. Re-measure on your own documents before
enabling it; 36 questions over 7 documents is a small sample.
