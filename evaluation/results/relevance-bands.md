# Calibration: relevance labels for search results

**Question.** Search My Knowledge labels each result High, Medium or Low relevance. A label must
mean something measurable, not an arbitrary cut of a score. What do the local reranker's scores
(`Xenova/ms-marco-MiniLM-L-6-v2`, raw logits) say about whether a passage is actually relevant?

**Setup.** Same corpus and dataset as the [baseline](baseline-retrieval.md). Every candidate the
reranker scores was kept instead of the top 5:
`python evaluation/evaluate.py --configs hybrid+rerank --rerank-top-k 20` (2026-10-02).
For the 30 answerable questions that is 510 (question, passage) pairs, 33 of them containing the
labelled evidence for the question. The 6 unanswerable questions add 97 pairs, none relevant.

| Reranker score | Passages (answerable questions) | Contain the evidence | Passages for unanswerable questions |
|---|---|---|---|
| **≥ 5** → High | 15 | **15 (100%)** | 0 |
| **−5 to 5** → Medium | 28 | 15 (54%) | 15 |
| **< −5** → Low | 467 | 3 (< 1%) | 82 |

Recall at each cut: ≥ 5 finds 45% of the evidence passages, ≥ −5 finds 91%.

**Bands.** High = score ≥ 5, Medium = −5 to 5, Low = below −5, defined in
`RELEVANCE_BANDS` ([reranking.py](../../backend/app/rag/reranking.py)).

- *High* is the only band that was never wrong here, so it is the one to trust.
- *Medium* is a coin flip: about half contain the evidence, and every high-scoring passage for an
  unanswerable question (top score 4.0) lands here. It means "worth reading", not "answers it".
- *Low* almost never contains the evidence. Search My Knowledge collapses these results instead
  of hiding them, because 3 of the 33 evidence passages scored that low: the meals passage for
  the "glass of wine" paraphrase (−10.3), and, for two two-part questions, passages that answer
  only one part (the hotel cap, −8.2; the travel policy's approval rule, −6.0).

**Request phrasing lowers scores.** The cross-encoder scores how well a passage answers the text
it is given, and the dataset's questions are plain questions. Phrased as a request, the same
search scored far lower (local model, one passage each, 2026-10-02):

| Query | Score | Band |
|---|---|---|
| expense approvals | 7.91 | High |
| Find everything related to expense approvals | 1.65 | Medium |
| travel booking | 0.75 | Medium |
| Show me all documents about travel booking | −7.69 | Low |

So Search My Knowledge strips request phrasing before searching (`topic_of()` in
[retrieval.py](../../backend/app/rag/retrieval.py), the `topic` search option) and shows the topic
it searched for. Queries that aren't requests ("What is the leave policy?", "for loops in python")
are left unchanged.

**Limits.** "Relevant" here means "contains the labelled evidence"; a passage on a related topic
counts as not relevant, so Medium understates topical relevance. 33 positives is a small sample,
and the corpus was written for the evaluation. The bands apply only to this model: scores from
other rerankers (Voyage returns 0..1) are on a different scale, so results reranked by any other
model get **no label** until their bands are measured the same way.
