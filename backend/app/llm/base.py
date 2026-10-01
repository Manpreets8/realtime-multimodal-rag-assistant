"""Provider-neutral LLM interface.

The application builds requests from the content parts below and never from a vendor's
wire format; each provider (see `app/llm/claude.py`) translates them. Adding a provider
means implementing `LLMProvider` and registering it in `app/llm/factory.py`, with no
change to the RAG, chat or image code.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.core.errors import AppError

# --- Errors (messages are safe to show to users; rendered by the AppError handler) ---------


class LLMError(AppError):
    status_code = 502
    code = "llm_error"
    message = "The AI model could not generate an answer."


class LLMNotConfiguredError(LLMError):
    status_code = 503
    code = "llm_not_configured"


class LLMUnavailableError(LLMError):
    status_code = 503
    code = "llm_unavailable"


class LLMTimeoutError(LLMError):
    status_code = 504
    code = "llm_timeout"


class LLMRefusalError(LLMError):
    status_code = 422
    code = "llm_refusal"


class VisionNotSupportedError(LLMError):
    status_code = 422
    code = "vision_not_supported"
    message = "The configured AI model cannot read images."


# --- Request content ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    media_type: str  # image/png, image/jpeg, image/gif or image/webp
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SourcePart:
    """A retrieved passage the model may quote and cite. Citations refer to sources by their
    position among all `SourcePart`s in the request, in message order."""

    text: str
    title: str | None = None


ContentPart = TextPart | ImagePart | SourcePart
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    parts: tuple[ContentPart, ...]

    @classmethod
    def user(cls, *parts: ContentPart | str) -> "Message":
        return cls("user", _parts(parts))

    @classmethod
    def assistant(cls, *parts: ContentPart | str) -> "Message":
        return cls("assistant", _parts(parts))

    @property
    def text(self) -> str:
        """The text parts joined (sources and images excluded)."""
        return "\n".join(part.text for part in self.parts if isinstance(part, TextPart))

    @property
    def images(self) -> list[ImagePart]:
        return [part for part in self.parts if isinstance(part, ImagePart)]

    @property
    def sources(self) -> list[SourcePart]:
        return [part for part in self.parts if isinstance(part, SourcePart)]


def _parts(parts: Sequence[ContentPart | str]) -> tuple[ContentPart, ...]:
    return tuple(TextPart(part) if isinstance(part, str) else part for part in parts)


# --- Response -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CitationSpan:
    """A span of the answer text supported by a quote from one source."""

    document_index: int  # position of the SourcePart in the request
    cited_text: str
    answer_start: int  # character offsets of the supporting span in `LLMResponse.text`
    answer_end: int
    # Offsets of `cited_text` within the source's text, when the provider reports them; unverified.
    source_start: int | None = None
    source_end: int | None = None


@dataclass(slots=True)
class LLMResponse:
    text: str
    citations: list[CitationSpan]
    model: str  # the model that produced the answer (may differ from the configured one on fallback)
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    request_id: str | None = None  # the provider's request ID, for support tickets
    truncated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class TextStream(Protocol):
    """Receives the answer as it is generated."""

    async def text(self, delta: str) -> None: ...

    async def restart(self) -> None:
        """Discard the text received so far (a fallback model is taking over)."""


class LLMProvider(Protocol):
    provider_name: str
    model_name: str
    supports_images: bool

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
        """`max_tokens` / `effort` override the provider defaults for this call (`effort` is a
        reasoning-depth hint: low, medium or high; providers without one ignore it). With
        `stream`, text is forwarded as it is generated; the returned response is still the
        complete, authoritative answer. With `json_schema`, the answer text is JSON matching
        that schema (callers still validate it); it cannot be combined with citable sources."""
        ...
