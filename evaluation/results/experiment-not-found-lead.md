# Experiment: labelling "not found" answers that cite related information

**Question.** How grounded are Claude's answers, and does the app label them correctly? This is
the first run of the answer-level evaluation (`--answers`): the production pipeline answers every
question, the app checks each cited quote against its source, and a Claude judge checks every claim
in the answer against the passages the model was given.

**Setup.** Same corpus and dataset as the [baseline](baseline-retrieval.md) (7 documents,
37 chunks, 36 questions of which 6 are unanswerable), hybrid retrieval + local reranker,
`claude-opus-5` answering and judging. One run each:
`python evaluation/evaluate.py --configs hybrid+rerank --answers` (2026-10-01).

| Metric | Before | After | How it is measured |
|---|---|---|---|
| Answered (answerable questions) | 100.0% | 100.0% | not labelled "not found" |
| Fact recall | 100.0% | 100.0% | expected facts in the answer (string match) |
| Faithfulness | 99.3% | 100.0% | claims supported by the retrieved passages (judge) |
| Answer relevance | 100.0% | 100.0% | judge rating |
| Answers with citations | 100.0% | 100.0% | answered questions |
| Quotes verified | 100.0% | 100.0% | cited quotes found word for word in the cited source |
| Cited sources relevant | 92.2% | 92.2% | cited sources containing labelled evidence |
| Evidence cited | 100.0% | 100.0% | labelled evidence covered by the citations |
| **Correct abstention** | **66.7%** | **100.0%** | unanswerable questions labelled "not found" |
| Latency p50 | 5.4 s | 5.4 s | end to end |

**What went wrong before.** The model's text was right every time. For "What is the stock option
vesting schedule?" (q31) and "What is the reimbursement rate for electric scooter rentals?" (q35) it
said the knowledge base doesn't cover it, then helpfully cited related policy (retirement-plan
vesting; mileage and transit rates). The app labelled an answer by whether it had citations, so
both showed **"Answered from your documents"**, contradicting the answer's own first sentence.

**Change.** The grounded prompt now asks the model to begin such answers with one fixed sentence,
*"The knowledge base doesn't contain this information."*, and the pipeline labels any answer that
opens with it `not_found` even if related passages are cited (`states_not_found()` in
[prompts.py](../../backend/app/rag/prompts.py); tolerant of case, curly apostrophes and Markdown).
The UI lists those passages under **Related sources** rather than "Sources".

**Result.** All 6 unanswerable questions open with the sentence and are labelled "not found"; none
of the 30 answerable questions does, so nothing was lost. Faithfulness 99.3% → 100% is judge
variation, not this change: the one unsupported claim before ("full-disk encryption protects data
on the device", q20) was an extrapolation the model happened not to repeat.

**Limits.** 6 unanswerable questions is a small sample. Detection depends on the model using the
exact sentence; if it paraphrases, the answer falls back to the previous rule (labelled by
citations), so the failure mode is the old behaviour, never a hidden answer. Both runs together:
about 153k input and 10k output tokens for the answers, plus the judge's calls.
