# RAG pipeline

How documents become searchable knowledge and how questions become grounded, cited answers. Back to the [README](../README.md).

## Knowledge bases and documents

| Endpoint | Description |
|---|---|
| `POST /api/v1/knowledge-bases` | Create (`name`, optional `description`). Names are unique per user, case-insensitively (409). |
| `GET /api/v1/knowledge-bases` | List your knowledge bases with document counts (`limit`, `offset`). |
| `GET /api/v1/knowledge-bases/{id}` | Details, including document counts per processing status. |
| `PATCH /api/v1/knowledge-bases/{id}` | Rename or change the description. |
| `DELETE /api/v1/knowledge-bases/{id}` | Delete the knowledge base, its documents and their files. |
| `GET /api/v1/knowledge-bases/{id}/documents` | List documents, newest first. |
| `POST /api/v1/documents/upload` | Multipart upload: `knowledge_base_id` and `file`. |
| `GET /api/v1/documents/upload-config` | Size limit and supported types (the UI uses this). |
| `GET /api/v1/documents/{id}` | Document metadata and processing status. |
| `GET /api/v1/documents/{id}/download` | The original file, sent as an attachment. |
| `DELETE /api/v1/documents/{id}` | Delete the document and its file. |

**Authorization.** Every query is filtered by the current user. Another user's knowledge base or document returns **404, not 403**, so IDs can't be probed to learn what exists.

**Upload pipeline:**
1. **Request size limit.** `MaxBodySizeMiddleware` rejects bodies over `MAX_FILE_SIZE` + 1 MB, using `Content-Length` when present and counting bytes for chunked uploads. FastAPI parses the multipart body before the route runs, so this is the only place an oversized upload can be stopped early.
2. **Filename and type.** The filename is sanitised: path components and control characters are stripped and the length is capped. The file type is decided by the extension (PDF, DOCX, TXT, Markdown), never by the client's `Content-Type`.
3. **Streaming to disk.** The file is written to a temporary `.part` file with a size cap, and hashed with SHA-256 as it streams.
4. **Content checks.** The bytes must match the extension: a `%PDF-` header for PDF, a zip containing `word/document.xml` for DOCX, and valid UTF-8 (or UTF-16 with a BOM) with no NUL bytes for TXT and Markdown. Empty files are rejected.
5. **Duplicate check.** The same content can't be uploaded twice into one knowledge base (409 names the existing file). A unique constraint also covers concurrent uploads.
6. **Commit.** The file is moved to `{user_id}/{kb_id}/{document_id}.{ext}` and the row is committed with status `uploaded`. On any failure, both the temporary and the final file are removed.

Files are stored under `UPLOAD_DIR` (default `documents/`) behind a small storage interface (`services/storage.py`), so an object-store backend can replace it.

**Status.** After upload, a document moves through `uploaded` → `processing` → `completed` or `failed`. See the ingestion section below.

## Document ingestion

```
upload ─► validate ─► queue ─► extract (text + metadata) ─► clean ─► chunk ─► embed ─► index ─► completed
                        │          │                                            │
                        │          └── ExtractionError (with a code) ───────────┴──► failed (reason + code)
                        └── recovered on restart (status lives in the DB, not the queue)
```

**What is tracked for each document.** The status (`uploaded` = queued, `processing`, `completed`, `failed`) is stored in PostgreSQL. While a document is processed, the worker also reports its live stage to Redis (`extracting`, `chunking`, `embedding` with *n*/*total*, `indexing`), which the UI shows as a step indicator. Upload progress is shown by the browser while the file is sent. A completed document stores:

| Field | Contents |
|---|---|
| `extracted_metadata` | Title, author and document date from the file's own properties (PDF metadata, Word core properties; for Markdown, the first `#` heading; for Word without a title, its first heading). Converter titles like "Microsoft Word - report.docx" are cleaned, placeholder titles ("Untitled") dropped, and control, zero-width and text-direction characters removed (they can disguise text). Plus word, character, section and table counts. |
| `processing_stats` | Milliseconds spent extracting, chunking, embedding and indexing, the total, the embedding model and the characters indexed. |
| `error_code` | On failure: `password_protected`, `damaged_file`, `no_text`, `too_many_pages`, `too_large`, `unsupported_type`, `file_missing` (the file must change, so the app hides Retry), or `embedding_failed` / `internal_error` (temporary, so Retry is offered). |

Documents processed before these fields existed keep working and gain them when re-processed.

| Stage | Implementation |
|---|---|
| **Queue** | A Redis Stream consumed by separate worker processes (`python -m app.workers.ingestion_worker`); see [Redis and background jobs](operations.md#redis-and-background-jobs). Uploads return immediately. (Phases 4–12 used an in-process asyncio queue.) |
| **Extract** | `rag/extraction.py`. **PDF** (PyMuPDF): per page, in reading order, with the page number kept for citations. Encrypted, corrupt and >2000-page PDFs are rejected with a reason. **DOCX** (python-docx): paragraphs and tables in document order, split into sections by heading ("Leave Policy > Carry over"), with a zip-bomb guard. **Markdown**: sections from `#` headings, ignoring `#` inside code fences. **TXT**: UTF-8 or UTF-16. |
| **Clean** | `rag/text_cleaning.py`: NFKC normalisation (PDF ligatures like "ﬁ" become "fi"), removal of control and zero-width characters, re-joining words hyphenated across PDF line breaks, and whitespace collapsing that keeps paragraphs. |
| **Chunk** | `rag/chunking.py`: a recursive splitter (paragraph → line → sentence → word → hard cut) packed to `CHUNK_SIZE` characters with `CHUNK_OVERLAP` overlap. A chunk never crosses a page or section, so each chunk cites exactly one location. A short heading-only section is folded into its first subsection, but sibling sections are never merged, so section labels stay accurate. |
| **Embed** | `rag/embeddings.py`: the `EmbeddingProvider` interface. **local** (default): fastembed/ONNX running `BAAI/bge-small-en-v1.5`, 384 dimensions, no PyTorch and no API key; the model (~65 MB) downloads once to `backend/.model_cache/`. **voyage**: the Voyage AI REST API with retries on 429/5xx. Documents and queries are embedded asymmetrically (`embed_documents` / `embed_query`). |
| **Store** | `document_chunks`: text, page, section, a `vector(384)` column with an **HNSW** index (cosine), and a generated `tsvector` column with a **GIN** index, used together by hybrid retrieval. Re-processing replaces a document's chunks in one transaction. |

Every stage is timed and logged with the document ID (`extraction_ms`, `chunking_ms`, `embedding_ms`, `duration_ms`, chunk and page counts). Unexpected errors show users a generic message, while the full traceback goes to the logs.

Endpoints added in this phase: `POST /documents/{id}/reprocess` (retry a failed document, or re-index a completed one) and `GET /documents/{id}/chunks` (inspect what was indexed).

**Changing the embedding model.** The vector column size is fixed by migration 0004 (384). The app refuses to start, with an explanation, if `EMBEDDING_DIMENSIONS` doesn't match. To switch to Voyage (1024 dimensions), add a migration that changes the column type, then re-process all documents.

**Licence note.** PyMuPDF is AGPL-3.0 licensed. That's fine for an open-source project; a closed-source deployment needs a commercial PyMuPDF licence or a different PDF parser.

## Document insights

For each processed document the app can generate, on request, a **short, detailed and technical summary**, **key points**, **topics**, **keywords** and **named entities** (people, organizations, locations, products, technologies, dates), shown in the document's Details panel alongside its statistics (pages, words, reading time, sections, passages). Implementation: [insights_service.py](../backend/app/services/insights_service.py).

```
POST /documents/{id}/insights ─► row: pending ─► job queue (kind=insights) ─► worker
     ─► passages in order ─► 1 LLM call, or N part calls + 1 merge call ─► validate ─► ground ─► ready
```

| Decision | Why |
|---|---|
| **On request, in the background worker** | Each generation costs LLM tokens, so it runs only when asked (`DOCUMENT_INSIGHTS_AUTO=true` generates after every upload). It runs as an `insights` job on the same Redis Stream as ingestion, with its own per-document lock, so a slow model call never blocks the API and a crashed worker's job is retried (and dead-lettered as a failed insight, never a failed document). |
| **Structured outputs** | The LLM interface takes a JSON schema (`json_schema`); the Claude provider sends it as `output_config.format`, so the response is valid JSON for the schema. It is still validated with Pydantic and cleaned (whitespace, duplicates, length and count limits). |
| **The model states the language first** | A `language` field comes first in the schema and the prompt ties every other field to it. Without it, a real run on an English document returned a Spanish analysis; with it, the analysis matches the document. |
| **Grounding check** | Keywords and entity names are kept only if they occur in the text the model read; the number removed is stored and shown. Topics are labels and are not checked. |
| **Long documents: map-reduce, then sampling** | Up to `INSIGHTS_CHARS_PER_CALL` characters go in one call. Longer documents are analysed in parts (in order) and the part analyses merged by a final call. Past `INSIGHTS_MAX_PARTS` parts, an even sample of passages across the document is analysed and the share actually read is stored as `coverage` and shown ("from 40% of the document"), never hidden. |
| **Regeneration keeps the old insights visible** | A new request sets the row to `pending` without clearing the content; the panel says it is regenerating. A failed run keeps the previous content and records the tokens it spent. |

Each generation's model, number of LLM calls, input and output tokens, coverage and time are stored with the insights and shown under them, and every call is also logged as an `ai_call` record ([providers.md](providers.md)).

## The full pipeline

Every answer goes through the same stages; each one's counts and timings are stored with the answer and shown under it as **How this answer was found**.

```
question
 ─► preprocessing          whitespace collapsed, length capped (normalize_query)
 ─► query rewriting        only when useful: a follow-up in a conversation, or an attached image (conversation.py)
 ─► metadata filters       optional: document IDs, file types, upload dates  ─► the allowed documents
 ─► semantic search  ─┐
 ─► keyword search   ─┴─► RRF fusion ─► similarity threshold (vector-only hits)
 ─► deduplication          near-duplicate passages removed (3-word shingles, Jaccard ≥ DEDUP_THRESHOLD)
 ─► reranking              the configured reranker (local cross-encoder or hosted API) scores every candidate
 ─► context selection      RERANK_MIN_SCORE (off by default), RERANK_TOP_K, CONTEXT_MAX_CHARS
 ─► prompt construction    one citable source block per passage
 ─► LLM ─► answer
 ─► citation validation    quotes checked word for word against their sources
```

| Stage | Where | Notes |
|---|---|---|
| **Metadata filters** | `retrieve(filters=...)`, `filters` on search, answer and chat requests | Resolved to the user's matching, indexed documents first, then applied inside both retrievers' SQL (so `TOP_K` candidates still come back). Other users' document IDs simply match nothing. The Search tab offers file-type filters; the API also takes document IDs and upload dates. |
| **Deduplication** | [context.py](../backend/app/rag/context.py) | The same text in two documents (two versions of a policy, a copy) would fill several of the five context slots with one fact. Near-duplicates are removed before the top candidates are cut, keeping the better-ranked copy. Adjacent chunks of one document share only their overlap and are unaffected (tested). On the evaluation corpus, which has no duplicates, every retrieval metric is unchanged (measured); a real run with a copied document removed the copy. |
| **Relevance threshold after reranking** | `select_context()` | Measured on the evaluation set: every threshold also removed relevant passages (even -10 drops recall@5 from 100% to 96.7%), so it is **off by default** and available as a trade-off. See [the experiment](../evaluation/results/experiment-rerank-threshold.md). |
| **Context selection** | `select_context()` | After the threshold: the best `RERANK_TOP_K` passages within `CONTEXT_MAX_CHARS` (the first passage is always kept). The evaluation uses the same function, so it measures exactly what answers use. |
| **Citation validation** | `check_citations()` in [pipeline.py](../backend/app/rag/pipeline.py) | Citations to a source that wasn't sent are dropped and counted. Every quote is looked up in its source; quotes not found word for word are kept but flagged (never given a guessed highlight position), and the trace shows "2 of 2 quotes found word for word". |

## Retrieval

`POST /api/v1/retrieval/search` with body `{"query", "knowledge_base_ids", "mode": "hybrid" | "vector" | "keyword", "limit", "filters", "options"}` returns the chunks the RAG pipeline will use as context. Each hit includes its document, page and section, cosine similarity, keyword rank and fused score, and the response includes per-stage timings. In the UI this is the **Search** tab of a knowledge base.

```
query ─► normalise ─► embed_query ─┬─► vector search (HNSW, cosine, TOP_K) ─┐
                                   └─► keyword search (full text, TOP_K)   ─┴─► RRF ─► threshold ─► top N
```

- **Vector search:** pgvector cosine distance on the HNSW index, filtered by user and knowledge base. `hnsw.iterative_scan` (pgvector 0.8+) keeps searching when the filter discards candidates, so filtered queries still return `TOP_K` rows.
- **Keyword search:** Postgres full text over the generated `tsvector`. The query's lexemes are OR-ed rather than AND-ed: a natural question like "what does ERR-4521 mean on my laptop" would match nothing if every word were required. Ranked with `ts_rank_cd`.
- **Fusion:** Reciprocal Rank Fusion (k = 60) by default. Chunks found by both retrievers rise to the top, and the two retrievers' incomparable scores never need normalising. `RETRIEVAL_FUSION=weighted` uses `HYBRID_ALPHA * similarity + (1 - HYBRID_ALPHA) * keyword score` instead, each min-max normalised within its list. Measured on the evaluation set, RRF was as good or better than every weight tried ([experiment](../evaluation/results/experiment-fusion.md)), so it stays the default.
- **Per-search overrides:** `options` (`candidates` per retriever 1–100, `similarity_threshold` 0–1, `fusion`, `alpha` 0–1) override the server settings for that search only, and the response's `parameters` reports what was actually used. The Search tab exposes them under **Advanced settings**. Chat and `/rag/answer` always use the server settings.
- **Relevance threshold:** a chunk is kept if its cosine similarity is at least `SIMILARITY_THRESHOLD`, **or** it matched the keyword search, because exact terms such as error codes and names count even when similarity is modest.
- **Isolation:** knowledge-base ownership is checked first (404 otherwise), and `retrieve()` also filters by `user_id`, as defence in depth.
- **Privacy:** logs record query length, candidate counts and timings, never the query text.

**Choosing the threshold (measured, not guessed).** With `bge-small-en-v1.5` over a 4-page sample handbook, using the best-matching chunk's similarity:

| Query set | Similarity of best chunk |
|---|---|
| 10 answerable questions (all top-1 on the correct page) | 0.61 – 0.84 |
| 6 off-topic questions ("capital of France") | 0.37 – 0.43 |
| 4 in-domain but unanswerable ("parental leave policy?") | 0.51 – 0.68 |

A threshold of **0.5** separates relevant from off-topic with margin on both sides. It **cannot** separate the unanswerable-but-on-topic questions; those must be caught by the grounded answer step in Phase 6, which says "not found". This is a small sample; Phase 14 evaluates on a proper dataset. A regression test (`pytest -m model`) pins the threshold to the real model.

**Known limitation.** For very short, context-free chunks, bge-small can rank a text containing a shared word ("days") slightly above a true paraphrase ("vacation" vs "annual leave"). The reranker in Phase 6 is meant to resolve such near-ties.

**Latency** (local, 4 documents, 30 hybrid searches through the HTTP API): p50 **88 ms**, p95 **123 ms**. Query embedding ~44 ms, vector search ~9 ms, keyword search ~3 ms (server p50).

## RAG answers

`POST /api/v1/rag/answer` with body `{"question", "knowledge_base_ids"}`. In the UI this is the **Ask** tab of a knowledge base.

```
question ─► hybrid retrieval (RERANK_CANDIDATES=20) ─► cross-encoder rerank ─► top RERANK_TOP_K (5)
         ─► grounded prompt: one citable `document` block per source ─► Claude ─► answer + citations
```

| Answer type | When |
|---|---|
| `knowledge_base` | The answer cites at least one retrieved source. |
| `not_found` | Nothing passed the relevance filter, so **no LLM call is made** and nothing is invented. Or the model answered without citing any source, meaning it reported the answer isn't in the documents. |
| `general` | No knowledge base selected: a general-knowledge answer, and the prompt forbids claiming to have read documents. |

**Grounding and hallucination control:**
- The system prompt requires every fact to come from the documents and be cited, a plain "not found" when the answer is missing, partial answers to say what's missing, and conflicting sources to be flagged. Document text is treated as data: instructions inside documents are ignored, which guards against prompt injection.
- Citations use Claude's **native citations** (`document` blocks with `citations.enabled`), not parsed `[1]` markers. Each citation returns the exact quoted text and the document index. Citations pointing at a source that was never sent are dropped and logged.
- The response includes the answer text, cited sources with quotes and the answer character ranges they support, every source given to the model, token usage, and timings for retrieval, reranking and the LLM.

**Reranking.** `Xenova/ms-marco-MiniLM-L-6-v2` via fastembed (80 MB, Apache-2.0, no key), chosen by measurement out of four candidates. On our test questions it put the correct passage first 9/9 and fixed the paraphrase near-tie the embedding model got wrong ("vacation days" vs a travel text that only shares "days"). Latency is about 10 ms per candidate on CPU (median 75 ms per question on the sample knowledge base). On the clean sample handbook, retrieval alone was already 9/9, so the measured benefit there is "no regression"; the gain shows on harder, shorter chunks. Rerank scores order results but **do not filter** them: unanswerable-but-on-topic questions scored higher than some relevant passages. If the reranker fails, the pipeline falls back to retrieval order. Set `RERANKER_PROVIDER=none` to disable it.

**Reranking providers.** Rerankers implement one `Reranker` protocol (`rerank(query, chunks, top_k)` returning passages with scores), built by `build_reranker()` from `RERANKER_PROVIDER`:

| Provider | Model (default) | Score | Notes |
|---|---|---|---|
| `local` | `Xenova/ms-marco-MiniLM-L-6-v2` | raw logit, unbounded | runs on CPU, no key; measured on the evaluation set |
| `voyage` | `rerank-2.5` | 0..1 relevance | Voyage AI API, `RERANKER_API_KEY`; tested against recorded API responses, **not yet measured on the evaluation set** |
| `none` | | none | keeps the retrieval order |

Scores are the model's own and are never rescaled or invented: if reranking is off or fails (after retries for 429/5xx), passages keep the retrieval order and have **no** score, and the answer trace says "Kept the retrieval order". Each answer records which model scored it (`reranker`) and how many passages it scored (`rerank_candidates`). Because the scales differ, a `RERANK_MIN_SCORE` chosen for one model is meaningless for another. The Search tab's **Advanced settings → Rerank results** runs the same reranker over a search, so its scores can be inspected per passage.

**Claude client** (`app/llm/claude.py`):
- The official `anthropic` async SDK with streaming (`messages.stream()` + `get_final_message()`); `MODEL_NAME` defaults to `claude-opus-5`.
- **Refusal fallback** (on by default, `LLM_REFUSAL_FALLBACK`): if Claude's safety classifiers decline a request, Anthropic re-runs it on its recommended fallback model (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). A remaining refusal becomes a clear 422, never a fake answer.
- **Errors:** SDK errors map to user-facing ones (timeout → 504, rate limit / overload / 5xx / network → 503, invalid key → 503 `llm_not_configured`, other 4xx → 502). Provider error text is logged, never returned.
- **Effort:** `LLM_EFFORT` is empty by default, so the API default applies. Lowering it to `medium`/`low` is the main latency and cost lever; measure answer quality before changing it.
- **Without `LLM_API_KEY`:** the app still starts, logs a warning, and `/rag/answer` returns a 503 explaining what to set. Upload, indexing and search keep working.

## Citations and source display

Every grounded answer shows **clickable citation markers** right after the text they support. Selecting a marker, a source in the list, or "View in context" on a search result opens the **source viewer**: a side drawer on desktop, a bottom sheet on mobile.

- **Exact highlighting.** The viewer shows the cited passage with the quoted text highlighted, plus the passages before and after it (`GET /api/v1/chunks/{id}?neighbors=1`).
- **Verified quote offsets.** Claude's citations report character offsets. The backend accepts them only if the text at those offsets matches the quote; otherwise it searches the passage for the quote, and if that also fails it returns no position. The UI never highlights a guessed location: unverified quotes are listed as quotes instead.
- **Open the original.** For PDFs, "Open page N" opens the file in a new tab at the cited page (`#page=N`) via `GET /documents/{id}/download?inline=true`. Only PDFs are served inline; every other type is always sent as an attachment, so an uploaded file can never render as a page in the app's origin. "Download" is available for every source.
- **"Not found" stays "not found".** When the documents don't answer the question, the model must open with *"The knowledge base doesn't contain this information."* The app then labels the answer "Not found in the knowledge base" even if it goes on to cite related passages, and lists those under **Related sources** ([experiment](../evaluation/results/experiment-not-found-lead.md)).
- **Only verified quotes are shown as quotes.** The Sources list previews a quote only if it was found word for word in the source; the viewer lists any other cited text separately, marked unverified.
- **Copy with sources.** Copies the answer as plain text followed by a `Sources:` list (`[1] handbook.pdf — Page 2`), with real page numbers or sections only (chunks never cross a page, so each passage has exactly one page).
- **Accessibility.** Markers are buttons whose accessible names include the source ("Source 1: handbook.pdf, Page 2"). The viewer is a modal dialog: Escape closes it, focus moves in and back, and the page behind is made `inert`.

## Chat and conversation history

| Endpoint | Description |
|---|---|
| `POST /api/v1/chat` | `{"message", "conversation_id"?, "knowledge_base_id"?}`. Starts a conversation when `conversation_id` is omitted; on an existing conversation, include `knowledge_base_id` only to switch (null = general chat). |
| `GET /api/v1/conversations` | Your conversations, most recent activity first, with knowledge base name, message count and last-message preview. |
| `GET /api/v1/conversations/{id}` | A conversation with all messages, citations and sources. |
| `PATCH /api/v1/conversations/{id}` | Rename, or change the knowledge base. |
| `DELETE /api/v1/conversations/{id}` | Delete the conversation and its messages. |

**Schema:** `conversations` → `messages` → `citations`. Assistant messages store their answer type, the search query used, model, token usage, timings and retrieval statistics. `citations` has one row per source given to the model, with a `cited` flag, the verified quotes, and a **snapshot** of the source (filename, page or section, text). If the document is later deleted or re-indexed, the links become NULL but the history still renders, and the source viewer shows the saved passage.

**Follow-up questions:**
- **Retrieval.** "What about the second one?" has nothing to search for on its own. When a conversation has history and a knowledge base, one small Claude call (effort `QUERY_REWRITE_EFFORT`, default `low`) rewrites the question into a standalone search query using the recent turns. If that call fails, the previous user question is prepended instead, so search still has context. The rewritten query is stored and shown under the answer ("Searched for: …").
- **Answering.** The last `CHAT_HISTORY_MESSAGES` messages (default 10, each capped at `CHAT_HISTORY_MESSAGE_CHARS`) are sent as plain turns before the grounded question. Only the newest turn carries documents, and the model answers the user's own wording.

**Atomic turns.** A question and its answer are committed together, and only after the answer succeeds. A timeout or model error saves nothing; the UI keeps the message with **Retry** and **Edit**, so history never holds unanswered questions. Timestamps are set in the application, not by `now()`, which is the transaction start time in Postgres and would give both messages the same timestamp.

**UI.** Chat (`/chat`, `/chat/:id`) has a conversation list (an overlay on phones), a knowledge-base picker (a new chat can be opened from a knowledge base's **Chat** button), message bubbles with inline citations and the source viewer, a thinking indicator, suggestions for an empty chat, and Enter to send / Shift+Enter for a new line.
