"""One structured `ai_call` log record per AI provider call.

Providers are wrapped where they are created (the `get_*_provider` factories), so every
implementation, including future ones, is measured the same way without instrumenting
each provider's code. A record carries what was called and how it went; never the
content (prompts, documents, audio, answers):

    {"message": "ai_call", "kind": "llm", "provider": "anthropic", "model": "claude-opus-5",
     "operation": "generate", "status": "ok", "latency_ms": 1834.2,
     "input_tokens": 2310, "output_tokens": 412, "request_id": "4f0c..."}

`status` is ok, error (with `error_type`) or cancelled (the user pressed Stop).
The request ID comes from the logging filter, so records can be joined to the request.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class AICallKind(StrEnum):
    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"


# (positional args, keyword args, result) -> extra fields for the record
UsageFn = Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]]


class InstrumentedProvider:
    """Transparent wrapper: the listed async methods are timed and logged; every other
    attribute (model_name, dimensions, warm_up...) is the wrapped provider's own."""

    def __init__(
        self,
        inner: Any,
        *,
        kind: AICallKind,
        provider: str,
        model: str,
        methods: Mapping[str, UsageFn | None],
    ) -> None:
        self.wrapped = inner
        self._kind = kind
        self._provider = provider
        self._model = model
        self._methods = methods

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.wrapped, name)
        if name not in self._methods:
            return attribute
        usage = self._methods[name]

        @functools.wraps(attribute)
        async def call(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await attribute(*args, **kwargs)
            except asyncio.CancelledError:
                self._log(name, started, "cancelled")
                raise
            except Exception as exc:
                self._log(name, started, "error", {"error_type": type(exc).__name__})
                raise
            self._log(name, started, "ok", usage(args, kwargs, result) if usage else {})
            return result

        return call

    def _log(self, operation: str, started: float, status: str, fields: dict[str, Any] | None = None) -> None:
        record = {
            "kind": self._kind.value,
            "provider": self._provider,
            "model": self._model,
            "operation": operation,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            **(fields or {}),
        }
        logger.log(logging.WARNING if status == "error" else logging.INFO, "ai_call", extra=record)


def unwrap(provider: Any) -> Any:
    """The provider inside an `InstrumentedProvider` (or the provider itself)."""
    return provider.wrapped if isinstance(provider, InstrumentedProvider) else provider


# --- Usage extractors for the provider interfaces ----------------------------------------------


def llm_usage(args: tuple[Any, ...], kwargs: dict[str, Any], response: Any) -> dict[str, Any]:
    return {
        "model": response.model,  # the model that answered (a fallback model may have taken over)
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "stop_reason": response.stop_reason,
        "citations": len(response.citations),
        "provider_request_id": response.request_id,
    }


def _first(args: tuple[Any, ...], kwargs: dict[str, Any], name: str) -> Any:
    return args[0] if args else kwargs.get(name)


def embedding_documents_usage(args: tuple[Any, ...], kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    return {"items": len(_first(args, kwargs, "texts") or [])}


def embedding_query_usage(args: tuple[Any, ...], kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    return {"items": 1}


def rerank_usage(args: tuple[Any, ...], kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    chunks = args[1] if len(args) > 1 else kwargs.get("chunks")
    return {"items": len(chunks or []), "returned": len(result)}


def transcription_usage(args: tuple[Any, ...], kwargs: dict[str, Any], transcript: Any) -> dict[str, Any]:
    return {"audio_seconds": round(transcript.duration_seconds, 2)}


def synthesis_usage(args: tuple[Any, ...], kwargs: dict[str, Any], audio: Any) -> dict[str, Any]:
    return {"characters": len(_first(args, kwargs, "text") or ""), "audio_bytes": len(audio)}
