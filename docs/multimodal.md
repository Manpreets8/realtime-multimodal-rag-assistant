# Multimodal pipeline and streaming

Images, voice input, voice output and real-time streaming. Back to the [README](../README.md).

## Images (vision)

| Endpoint | Description |
|---|---|
| `POST /api/v1/images` | Upload an image (multipart `file`) to attach to a chat message. |
| `GET /api/v1/images/{id}/content` | The image, owner only (served with its decoded media type and `nosniff`). |
| `DELETE /api/v1/images/{id}` | Delete an uploaded image that hasn't been sent. |
| `POST /api/v1/chat` with `image_ids` | Send up to `MAX_IMAGES_PER_MESSAGE` (4) images with a message. The text may be empty. |
| `POST /api/v1/multimodal/image` | One-off question about an image (multipart `file`, `question`, optional `knowledge_base_id`). Processed in memory, not saved. |

**Validation (`app/multimodal/images.py`).** Images are decoded with Pillow; the extension and Content-Type are never trusted.
- Only **PNG, JPEG, GIF and WebP** are accepted. BMP and TIFF are rejected with **415**. **SVG and other markup are always rejected**, because SVG can carry script.
- A corrupt or truncated file, or a PDF renamed to `.png`, gets **422**. A file over `MAX_IMAGE_SIZE` (5 MB) gets **413**. Images are limited to 8000 px per side.
- Decompression bombs (tiny files that decode to huge pixel counts) are refused before a full decode.

**Preparation for the model.** A separate copy is made for the model:
1. **EXIF rotation** is applied, so phone photos arrive upright.
2. The image is **downscaled** to `IMAGE_MODEL_MAX_EDGE` (1568 px, Anthropic's recommended long edge; the API would downscale anyway, so larger images only add upload time and latency). It is never upscaled.
3. It is **re-encoded**, which drops metadata such as camera details and GPS location. JPEGs stay JPEG; everything else becomes PNG to keep screenshot text crisp. Animated GIFs send their first frame.

**Answer types.**
- `image`: answered from the image alone. This covers no knowledge base selected, nothing relevant found in the documents, or no document cited.
- `multimodal`: the image plus cited knowledge-base documents.

A dedicated prompt requires the model to describe only what is visible, say when text is unreadable, quote error text exactly, label fixes that go beyond the image as general guidance, and not identify people.

**Searching with an image.** With a knowledge base selected, the query-writing call receives the image too, so its **visible text** (error codes, titles) becomes part of the search query. A screenshot of `ERR-4521` finds the troubleshooting document even if the user only asked "how do I fix this?".

**Follow-ups.** Images from recent user messages (up to `MAX_HISTORY_IMAGES`, most recent first) are re-sent with later turns, so "what about the second section?" still refers to the diagram.

**Failure behaviour.** If a stored image can't be read at answer time, the request fails with `invalid_image` and **the model is not called**: the app never answers as if it had seen an image it didn't. Nothing is saved, and the UI keeps the message and its images for Retry. Deleting a conversation deletes its image files. An image can be sent only once.

**Cleanup.** Images uploaded but never sent are deleted with their files after `ORPHAN_IMAGE_TTL_HOURS` (24 h) by the workers' maintenance job.

## Voice input

**Flow.** Mic button → the browser records → `POST /api/v1/voice/transcribe` → the transcript is **placed in the message box for review** → the user edits it if needed and presses Send. Nothing is sent automatically, so a misheard word never becomes a question without the user seeing it.

**Speech-to-text (`app/multimodal/speech.py`).**
- **Providers.** `STT_PROVIDER=local` (default) runs **faster-whisper** (CTranslate2, int8 on CPU) with no API key; the model downloads once to `backend/.model_cache/whisper`. `STT_PROVIDER=openai` uses OpenAI's transcription API with `STT_API_KEY`; that provider is tested against a mocked API only.
- **Format detection.** Audio is decoded with PyAV/FFmpeg; the file extension and Content-Type are never trusted. Browser recordings (WebM/Ogg Opus, MP4/AAC) and WAV/MP3/M4A all work. A file with no audio stream gets 415; unreadable audio gets 422.
- **Limits.** `MAX_AUDIO_SIZE` (10 MB) and `MAX_AUDIO_SECONDS` (120 s). Decoding stops as soon as a recording passes the time limit, so a small but very long file can't tie up the CPU. Clips under 0.3 s are refused, and `STT_CONCURRENCY` (default 1) caps simultaneous transcriptions.
- **No hallucinated text.** Voice-activity detection is always on. Whisper is known to invent text for silence; in testing, the `base` model transcribed three seconds of silence as *"You"* with VAD off and returned nothing with it on. Silent recordings return **422 `no_speech`** ("No speech was detected. Check your microphone…").

**Model choice (measured on this machine, CPU, for a 6.6 s spoken question).**

| Model | Transcription time | Accuracy on the test clip |
|---|---|---|
| tiny | 1.6 s | word-perfect |
| **base (default)** | 2.9 s cold / **~1 s warm** | word-perfect |
| small | 8.3 s | word-perfect |

The test speech came from the Windows speech synthesizer: clean and accent-free, which is the easy case. All three models were perfect on it, so this table is **not** a real-world accuracy claim. `base` is the default because larger Whisper models are more robust on real voices, accents and noise, and `base` still runs faster than real time. Set `STT_MODEL` to trade speed for accuracy.

**Browser recording (`useVoiceRecorder`).**
- MediaRecorder picks the best supported format (Opus in Chrome and Firefox, MP4 in Safari), with echo cancellation and noise suppression on.
- A timer shows the recording length, and recording stops automatically at the limit. **Cancel** discards the recording.
- The microphone is always released after recording, including when the user navigates away.
- Clear messages cover a blocked microphone, no microphone, a microphone in use by another app, an unsupported browser, and a non-HTTPS page (browsers only allow the microphone on secure pages).

**Verified end to end.** A real Chromium browser recorded through its real `getUserMedia` → MediaRecorder path, using a WAV file as the microphone input. The live server transcribed it with the real `base` model in about 1.1 s after Stop. A committed fixture (`tests/fixtures/spoken_question.webm`) and a `model`-marked test pin this: word-perfect transcription, and `no_speech` for silence.

## Voice output

**Flow.** Each assistant answer has a **Listen** button → `POST /api/v1/voice/synthesize` with the answer text → MP3 audio → the answer's player shows **Play/Pause, Stop, Replay**, a seekable progress bar and the elapsed / total time.

- **Never auto-plays.** No audio is requested or played until the user presses Listen. Audio starts only from that click.
- **One answer at a time.** Starting another answer pauses the one that was playing.
- **Clean-up.** Leaving the conversation stops playback, cancels a pending request and frees the audio.

**Text-to-speech (`app/multimodal/tts.py`).**
- **Providers.** `TTS_PROVIDER=local` (default) runs **Piper** (ONNX Runtime on CPU) with no API key. The voice `en_US-lessac-medium` (~63 MB) downloads once to `backend/.model_cache/piper` and is loaded at startup. `TTS_PROVIDER=openai` uses OpenAI's speech API with `TTS_API_KEY` (`TTS_VOICE` defaults to `alloy`); that provider is tested against a mocked API only.
- **Speaks the answer, not the markup.** Before speaking, the text is cleaned: citation markers (`[1]`, `[1, 2]`), bold and italic markers, headings, list bullets, links and URLs are removed, code blocks become "(code omitted)", and list items and lines become sentences. Speaking `**25 days** [1]` would otherwise produce "asterisk asterisk… one".
- **Limits.** `TTS_MAX_CHARS` (4000). Longer text is cut at a sentence boundary; the response header `X-Speech-Truncated: true` makes the player show "Reading the first part only". `TTS_CONCURRENCY` (default 1) caps simultaneous syntheses.
- **Cache.** The last `TTS_CACHE_ENTRIES` (64) results are kept in memory, keyed by provider, voice and cleaned text. Re-listening costs nothing, and Replay reuses the audio already in the browser.
- **Output.** MP3, mono, 64 kbps: about 80 KB for 10 seconds of speech, and plays in every browser.

**Measured on this machine (CPU).**

| Case | Time |
|---|---|
| Voice load at startup | 1.6 s |
| 10.1 s of speech (a two-sentence answer), first request | 0.40 s (~25× faster than real time) |
| Same answer again (cache hit) | < 1 ms server-side |

**Verified end to end.** A real Chromium browser, with the default autoplay policy, loaded the live app.
- No audio was requested before Listen was clicked.
- Listen → playing in 90 ms. That audio was already cached by the API checks run just before; uncached, synthesis adds about 0.4 s for this answer.
- Pause held the position; Play resumed; Stop reset to 0:00; Replay restarted without a second request.
- Starting a second answer paused the first, and leaving the conversation paused everything.
- At phone width (390 px) there was no horizontal overflow.

The answer in that run was a test double, because no `LLM_API_KEY` is configured. The audio came from the real server and Piper. The generated MP3, transcribed by the app's own Whisper, came back word for word ("25" and "5" as digits). A `model`-marked test pins this round trip.

## Real-time streaming (WebSocket)

Chat messages go over one WebSocket, `/api/v1/ws/chat`, shared by the app. The user sees each stage ("Searching your documents…", "Ranking the most relevant passages…", "Writing an answer from 5 passages…") and then the answer **word by word as Claude writes it**. A **Stop** button replaces Send while an answer is being written. When the answer finishes, the saved message, with its citations, replaces the streamed text.

**Protocol** (JSON text frames; full reference in [chat_socket.py](../backend/app/api/routes/chat_socket.py)):

| Direction | Frames |
|---|---|
| client → server | `auth` (first frame, within 10 s) · `chat` (`id` + the same fields as `POST /chat`) · `cancel` |
| server → client | `ready` · `status` (`rewriting` / `retrieving` / `reranking` / `generating`) · `sources` · `delta` · `restart` · `done` (the saved turn, the same shape as `POST /chat`) · `cancelled` · `error` (the standard error envelope) |

**Design decisions.**
- **The token goes in the first frame, not the URL.** Browsers can't set headers on a WebSocket, and a token in the query string ends up in access logs and browser history. The token is re-checked before every turn, so logging out or expiry ends the socket session (close code 4401) and signs the browser out.
- **Origin check.** Browsers don't apply CORS to WebSockets, so any website could otherwise open the socket from the user's browser. The handshake is refused unless `Origin` is in `CORS_ORIGINS`. Non-browser clients send no `Origin` and are allowed; they still need a valid token.
- **Stop and disconnect save nothing.** Cancelling closes the Claude stream, so no more tokens are generated or billed, and no message is stored. This matches the REST rule that a turn is saved only when it completes. The message goes back into the composer for editing.
- **One turn at a time per connection.** A second `chat` frame while one is running gets a `busy` error.
- **The streamed text is a preview; `done` is authoritative.** Citations arrive with the saved message. If Claude's refusal fallback switches models mid-answer, a `restart` frame clears the declined model's partial text.
- **REST fallback.** If the WebSocket can't be used (a proxy blocks it, or the server is unreachable), the message is sent with `POST /chat`: no live progress, but the same saved turn. After a failed connection the app uses REST for 60 s rather than waiting on the socket for every message.
- **Accessibility.** The streamed text is `aria-live="off"`, so screen readers aren't sent every word. The stage line is a `status`, and the finished answer is announced once.
- **Limits.** Frames over 64 KB close the connection (1009). Unknown or invalid frames get an `error` and the connection stays open.

**Two bugs found and fixed while building this.**
- **Fallback text.** After a mid-stream refusal fallback, the response holds the declined model's partial text, then a `fallback` block, then the fallback model's answer. The Phase 6 parser joined all text blocks, so the declined model's words would have been saved as part of the answer. Only the text after the last `fallback` block is used now, and a test pins this with a real SDK event stream.
- **Message order.** Messages are ordered by `created_at`. On Windows the wall clock advances in ticks of about 1–16 ms, so a fast turn gave the question and the answer the same timestamp, and their order then depended on a random UUID. This caused an intermittent test failure (about 1 in 10 runs), and in real use a fast "not found" answer could have shown above its question. Timestamps are now forced to increase strictly within a conversation, and a frozen-clock test pins this.

**Verified end to end.** With no Anthropic key configured, a local **mock of the Anthropic streaming API**, a verification script outside the repository, stood in for Claude. It sends genuine server-sent events, one word every 120 ms, with a citation. Everything else was real: the Anthropic SDK's stream parsing, the backend, local embeddings and reranking, the Vite WebSocket proxy, and Chromium.
- The token was never in the URL, and the first frame was `auth`.
- The stages appeared in order, and the first words showed **0.37 s** after Enter, while the full answer took 3.5 s at the mock's pace. The saved answer rendered with its citation marker and the "Answered from your documents" badge.
- A follow-up showed the `rewriting` stage.
- **Stop:** the mock logged that the backend closed the Claude stream after 7 of 29 words, and no conversation was saved.
- With the WebSocket blocked, the message went through `POST /chat`.
- A page on another origin could not open the socket (handshake refused).
- At phone width there was no horizontal overflow, and there were no page errors.

With a real `LLM_API_KEY`, the only difference is real Claude output; the path is the same.
