# RAG evaluation: northwind-policies v1

- Run: 2026-09-27T09:27:23+00:00 (20.8 s)
- Corpus: 7 documents, 37 chunks (fingerprint `ad6d9b6cda54acca`)
- Questions: 36 (30 answerable, 6 unanswerable)
- Settings: embedding=local/BAAI/bge-small-en-v1.5, reranker=Xenova/ms-marco-MiniLM-L-6-v2, chunk_size=1000, chunk_overlap=150, top_k=20, rerank_candidates=20, rerank_top_k=5, similarity_threshold=0.5, model=None

## Retrieval (answerable questions)

| Configuration | Hit@1 | Hit@3 | Hit@5 | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| **vector** | 86.7% | 96.7% | 96.7% | 81.7% | 96.7% | 96.7% | 0.917 | 0.917 | 15.72 | 18.63 |
| **keyword** | 86.7% | 93.3% | 93.3% | 81.7% | 91.7% | 93.3% | 0.900 | 0.892 | 6.41 | 16.34 |
| **hybrid** | 93.3% | 100.0% | 100.0% | 88.3% | 100.0% | 100.0% | 0.961 | 0.958 | 16.15 | 18.96 |
| **hybrid+rerank** | 93.3% | 100.0% | 100.0% | 88.3% | 100.0% | 100.0% | 0.967 | 0.960 | 579.95 | 673.32 |

### Recall@3 by question category

| Configuration | factual (16) | keyword (3) | multi_hop (4) | paraphrase (7) |
|---|---|---|---|---|
| **vector** | 100.0% | 66.7% | 100.0% | 100.0% |
| **keyword** | 100.0% | 66.7% | 87.5% | 85.7% |
| **hybrid** | 100.0% | 100.0% | 100.0% | 100.0% |
| **hybrid+rerank** | 100.0% | 100.0% | 100.0% | 100.0% |

### Unanswerable questions at retrieval

| Configuration | Nothing retrieved | Mean chunks retrieved |
|---|---|---|
| **vector** | 0.0% | 5.0 |
| **keyword** | 0.0% | 4.2 |
| **hybrid** | 0.0% | 5.0 |
| **hybrid+rerank** | 0.0% | 5.0 |

### Can a score threshold detect unanswerable questions?

Top result's score: lowest among answerable questions vs highest among unanswerable ones.

| Configuration | Score | Answerable min | Unanswerable max | Separable |
|---|---|---|---|---|
| **vector** | similarity | 0.573 | 0.744 | no (6 overlap) |
| **hybrid** | similarity | 0.474 | 0.738 | no (6 overlap) |
| **hybrid+rerank** | rerank_score | -9.871 | 4.001 | no (4 overlap) |

### Misses (evidence not fully retrieved in the top 5)

- `vector` q24 (keyword): What is Keeper? (recall 0.0%)
- `keyword` q23 (paraphrase): Can I claim a glass of wine at dinner on a business trip? (recall 0.0%)
- `keyword` q26 (keyword): What is the response time for a SEV2? (recall 0.0%)
