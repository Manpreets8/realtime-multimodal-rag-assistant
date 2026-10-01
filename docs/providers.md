# AI provider layer

How Mindora AI keeps its AI services swappable, and how every AI call is measured. Back to the [README](../README.md).

## Interfaces

Each AI capability is used through an interface (a Python `Protocol`). The implementation is chosen by an environment variable and built by one factory function; the rest of the code never names a vendor.

| Capability | Interface | Implementations (setting) | Factory |
|---|---|---|---|
| LLM | `LLMProvider` ([base.py](../backend/app/llm/base.py)) | `anthropic` (`LLM_PROVIDER`) | [llm/factory.py](../backend/app/llm/factory.py) |
| Vision | `LLMProvider` (image parts) | the LLM provider, when `supports_images` | [llm/factory.py](../backend/app/llm/factory.py) |
| Embeddings | `EmbeddingProvider` | `local` (fastembed), `voyage` (`EMBEDDING_PROVIDER`) | [rag/embeddings.py](../backend/app/rag/embeddings.py) |
| Reranking | `Reranker` | `local` (cross-encoder), `voyage` (hosted API), `none` (`RERANKER_PROVIDER`) | [rag/reranking.py](../backend/app/rag/reranking.py) |
| Speech-to-text | `SpeechProvider` | `local` (faster-whisper), `openai` (`STT_PROVIDER`) | [multimodal/speech.py](../backend/app/multimodal/speech.py) |
| Text-to-speech | `TTSProvider` | `local` (Piper), `openai` (`TTS_PROVIDER`) | [multimodal/tts.py](../backend/app/multimodal/tts.py) |

`GET /api/v1/system/providers` (signed-in users) reports which provider and model serves each capability and whether it is configured. It is built from settings only ([core/providers.py](../backend/app/core/providers.py)): it never loads a model, calls an API or returns a secret.

## Provider-neutral messages

The RAG, chat and image code build LLM requests from neutral content parts, never from a vendor's wire format:

| Part | Meaning | Claude translation |
|---|---|---|
| `TextPart(text)` | Plain text | `text` block (a text-only turn is sent as a plain string) |
| `ImagePart(media_type, data)` | Raw image bytes, already validated and downscaled | base64 `image` block |
| `SourcePart(text, title)` | A retrieved passage the model may quote and cite | `document` block with native citations enabled |

A `Message` is a role (`user` or `assistant`) plus its parts. The response is also neutral: an `LLMResponse` with the answer text, `CitationSpan`s (which answer span cites which source, by position), token usage and latency. The translation lives only in [llm/claude.py](../backend/app/llm/claude.py) (`to_anthropic_messages`), and a test pins the exact request body Anthropic receives.

**Vision is part of the LLM interface, not a separate provider.** A question about a screenshot is answered in one multimodal call that sees the image and the retrieved documents together. A separate "describe the image, then answer" step would lose detail the question depends on. Providers declare `supports_images`, and a request with images to a provider without vision fails with a clear `vision_not_supported` error (422) before anything is sent or saved.

### Adding an LLM provider

1. Implement `LLMProvider` in a new module: translate `Message` parts to the vendor's format, map the vendor's errors to the `LLMError` subclasses, and return an `LLMResponse`.
2. Add a value to `LLMProviderName` in [config.py](../backend/app/core/config.py) and a branch in `build_llm_provider`.
3. Add its credentials check to `llm_configured` in [core/providers.py](../backend/app/core/providers.py).

Nothing in `rag/`, `services/` or `api/` changes. One design dependency to know: grounded answers rely on citations returned by the provider (Claude's native citations). A provider without them would need to produce `CitationSpan`s another way, for example from numbered source markers in the answer.

The evaluation judge ([evaluation/judge.py](../backend/app/evaluation/judge.py)) deliberately uses the Anthropic SDK directly. It is an offline grading tool that relies on forced tool calls for structured scores, not part of the application.

## Measuring every AI call

Every provider a factory builds is wrapped by `InstrumentedProvider` ([core/ai_calls.py](../backend/app/core/ai_calls.py)). The wrapper is transparent (other attributes pass through) and writes one structured `ai_call` log record per call:

```json
{"message": "ai_call", "request_id": "2b38200802aebe71489f97d16a9295a8",
 "kind": "llm", "provider": "anthropic", "model": "claude-opus-5", "operation": "generate",
 "status": "ok", "latency_ms": 1834.2, "input_tokens": 2310, "output_tokens": 412,
 "stop_reason": "end_turn", "citations": 3, "provider_request_id": "req_..."}
```

| Kind | Extra fields |
|---|---|
| `llm` | `input_tokens`, `output_tokens`, `stop_reason`, `citations`, `provider_request_id`; `model` is the model that answered (a fallback model may take over) |
| `embedding` | `items` (texts embedded) |
| `rerank` | `items` (candidates), `returned` |
| `speech_to_text` | `audio_seconds` |
| `text_to_speech` | `characters`, `audio_bytes` |

- `status` is `ok`, `error` (with `error_type`, logged as a warning) or `cancelled` (the user pressed Stop).
- The `request_id` joins each record to the HTTP request that caused it.
- Records never contain content: no prompts, passages, audio or answers.
- Wrapping at the factory means a new provider is measured without any code of its own. The reranker is not wrapped when reranking is turned off (`none` does no work).

These records are the raw data for usage, cost and latency reporting (phases 22 and 23).
