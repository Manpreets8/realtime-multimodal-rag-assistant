"""Claude (Anthropic Messages API): the `LLMProvider` implementation for LLM_PROVIDER=anthropic.

Uses the official async SDK with streaming (`.stream()` + `get_final_message()`),
which avoids HTTP timeouts on long generations; with a `TextStream`, text deltas are
forwarded as they arrive (WebSocket chat). Requests go through the beta namespace to
enable server-side refusal fallbacks: if Claude's safety classifiers decline a request,
Anthropic re-runs it on its recommended fallback model instead of failing. A fallback
can happen mid-stream: the declined model's partial text is followed by a `fallback`
block and the fallback model's answer. Only the text after the last `fallback` block
is the answer, and streams are told to discard what they received so far.

Citations come from the API's native citations feature: each `SourcePart` is sent as a
`document` block with citations enabled, and cited text blocks in the response carry
`char_location` citations pointing back at a document index.
"""

import base64
import logging
import time
from collections.abc import Sequence
from typing import Any

import anthropic

from app.core.config import Settings
from app.llm.base import (
    CitationSpan,
    ImagePart,
    LLMError,
    LLMNotConfiguredError,
    LLMRefusalError,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    SourcePart,
    TextPart,
    TextStream,
)

logger = logging.getLogger(__name__)

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _to_anthropic_part(part: TextPart | ImagePart | SourcePart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": part.media_type,
                "data": base64.standard_b64encode(part.data).decode("ascii"),
            },
        }
    # A citable source: a plain-text document block with native citations enabled.
    block: dict[str, Any] = {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": part.text},
        "citations": {"enabled": True},
    }
    if part.title:
        block["title"] = part.title
    return block


def to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Neutral messages -> Messages API format. A text-only turn is sent as a plain string."""
    converted = []
    for message in messages:
        if len(message.parts) == 1 and isinstance(message.parts[0], TextPart):
            content: str | list[dict[str, Any]] = message.parts[0].text
        else:
            content = [_to_anthropic_part(part) for part in message.parts]
        converted.append({"role": message.role, "content": content})
    return converted


def _parse_message(message: Any, latency_ms: float) -> LLMResponse:
    """Flatten text blocks into one answer and translate block-level citations into answer offsets."""
    parts: list[str] = []
    citations: list[CitationSpan] = []
    length = 0
    for block in message.content:
        if block.type == "fallback":
            # Everything before this block came from a model that declined: not part of the answer.
            parts, citations, length = [], [], 0
            continue
        if block.type != "text":
            continue  # thinking blocks are not part of the answer
        start, length = length, length + len(block.text)
        parts.append(block.text)
        for citation in block.citations or []:
            document_index = getattr(citation, "document_index", None)
            if document_index is None:
                continue
            citations.append(
                CitationSpan(
                    document_index,
                    citation.cited_text,
                    start,
                    length,
                    source_start=getattr(citation, "start_char_index", None),
                    source_end=getattr(citation, "end_char_index", None),
                )
            )
    text = "".join(parts)
    # Strip surrounding whitespace, shifting citation offsets to match.
    lead = len(text) - len(text.lstrip())
    text = text.strip()
    if lead:
        citations = [
            CitationSpan(
                c.document_index,
                c.cited_text,
                max(c.answer_start - lead, 0),
                max(c.answer_end - lead, 0),
                c.source_start,
                c.source_end,
            )
            for c in citations
        ]
    usage = message.usage
    return LLMResponse(
        text=text,
        citations=citations,
        model=message.model,
        stop_reason=message.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=latency_ms,
        request_id=getattr(message, "_request_id", None),
        truncated=message.stop_reason == "max_tokens",
    )


class ClaudeClient:
    """`LLMProvider` for Anthropic's Claude."""

    provider_name = "anthropic"
    supports_images = True

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        max_tokens: int,
        effort: str = "",
        refusal_fallback: bool = True,
    ) -> None:
        self._client = client
        self.model_name = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._refusal_fallback = refusal_fallback

    async def generate(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        max_tokens: int | None = None,
        effort: str | None = None,
        stream: TextStream | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {}
        output_config: dict[str, Any] = {}
        effort = self._effort if effort is None else effort
        if effort:
            output_config["effort"] = effort
        if json_schema is not None:
            # Structured outputs: the response text is guaranteed to be JSON valid for the schema.
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            options["output_config"] = output_config
        if self._refusal_fallback:
            options["betas"] = [REFUSAL_FALLBACK_BETA]
            options["fallbacks"] = "default"

        started = time.perf_counter()
        try:
            async with self._client.beta.messages.stream(
                model=self.model_name,
                max_tokens=max_tokens or self._max_tokens,
                system=system,
                messages=to_anthropic_messages(messages),
                **options,
            ) as events:
                if stream is not None:
                    async for event in events:
                        if event.type == "content_block_start" and event.content_block.type == "fallback":
                            await stream.restart()
                        elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                            await stream.text(event.delta.text)
                message = await events.get_final_message()
        # Most specific first; the SDK has already retried 408/409/429/5xx and connection errors.
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("The AI model took too long to respond. Please try again.") from exc
        except anthropic.AuthenticationError as exc:
            logger.error("llm_authentication_failed")
            raise LLMNotConfiguredError(
                "The AI model is not configured correctly (invalid API key)."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailableError("The AI model is busy right now. Please try again shortly.") from exc
        except anthropic.APIStatusError as exc:
            logger.error(
                "llm_api_error",
                extra={
                    "status_code": exc.status_code,
                    "error": str(exc)[:500],
                    "anthropic_request_id": exc.request_id,
                },
            )
            if exc.status_code >= 500:
                raise LLMUnavailableError(
                    "The AI model is temporarily unavailable. Please try again."
                ) from exc
            raise LLMError("The AI model could not process this request.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("Could not reach the AI model. Please try again.") from exc

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        # A refusal is an HTTP 200: check before reading the content.
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            logger.warning(
                "llm_refusal",
                extra={
                    "category": getattr(details, "category", None),
                    "anthropic_request_id": getattr(message, "_request_id", None),
                },
            )
            raise LLMRefusalError("The AI model declined to answer this request.")

        # Usage and latency are logged for every call by the provider wrapper (app/core/ai_calls.py).
        return _parse_message(message, latency_ms)


def create_claude_provider(settings: Settings) -> ClaudeClient:
    """Raises LLMNotConfiguredError when LLM_API_KEY is not set."""
    if settings.llm_api_key is None:
        raise LLMNotConfiguredError(
            "The AI model is not configured. Set LLM_API_KEY (an Anthropic API key) in .env and restart."
        )
    client = anthropic.AsyncAnthropic(
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )
    return ClaudeClient(
        client,
        model=settings.model_name,
        max_tokens=settings.llm_max_tokens,
        effort=settings.llm_effort,
        refusal_fallback=settings.llm_refusal_fallback,
    )
