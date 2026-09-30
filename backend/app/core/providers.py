"""The AI provider catalog: which implementation serves each capability.

| Capability     | Interface           | Implementations (setting)             | Factory                  |
|----------------|---------------------|---------------------------------------|--------------------------|
| LLM            | `LLMProvider`       | anthropic (LLM_PROVIDER)              | app/llm/factory.py       |
| Vision         | `LLMProvider`       | the LLM provider, if it reads images  | app/llm/factory.py       |
| Embeddings     | `EmbeddingProvider` | local, voyage (EMBEDDING_PROVIDER)    | app/rag/embeddings.py    |
| Reranking      | `Reranker`          | local, none (RERANKER_PROVIDER)       | app/rag/reranking.py     |
| Speech-to-text | `SpeechProvider`    | local, openai (STT_PROVIDER)          | app/multimodal/speech.py |
| Text-to-speech | `TTSProvider`       | local, openai (TTS_PROVIDER)          | app/multimodal/tts.py    |

Vision is part of the LLM interface rather than a separate provider: an image question is
answered in one multimodal call together with the retrieved documents, which a separate
"describe the image, then answer" step would lose. Every provider built by a factory is
wrapped by `app/core/ai_calls.py`, so each call is timed and logged the same way.

This module describes the configuration only: it never creates a provider, loads a model
or calls an API, so it is cheap and safe to serve.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.config import (
    EmbeddingProviderName,
    LLMProviderName,
    RerankerProviderName,
    Settings,
    SpeechProviderName,
    TTSProviderName,
)

Capability = Literal["llm", "vision", "embeddings", "reranking", "speech_to_text", "text_to_speech"]
Runs = Literal["local", "api", "off"]

# LLM providers whose models accept images.
_VISION_CAPABLE = {LLMProviderName.ANTHROPIC}


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    capability: Capability
    provider: str
    model: str
    runs: Runs  # on this machine, through a hosted API, or turned off
    configured: bool  # credentials present (always true for local and "off")


def llm_configured(settings: Settings) -> bool:
    if settings.llm_provider is LLMProviderName.ANTHROPIC:
        return settings.llm_api_key is not None
    return False  # pragma: no cover - the settings enum admits no other value


def describe_providers(settings: Settings) -> list[ProviderInfo]:
    llm_ok = llm_configured(settings)
    llm = settings.llm_provider.value
    vision = settings.llm_provider in _VISION_CAPABLE

    embeddings_local = settings.embedding_provider is EmbeddingProviderName.LOCAL
    reranking_on = settings.reranker_provider is not RerankerProviderName.NONE
    stt_local = settings.stt_provider is SpeechProviderName.LOCAL
    tts_local = settings.tts_provider is TTSProviderName.LOCAL

    return [
        ProviderInfo("llm", llm, settings.model_name, "api", llm_ok),
        ProviderInfo(
            "vision",
            llm if vision else "none",
            settings.model_name if vision else "",
            "api" if vision else "off",
            llm_ok or not vision,
        ),
        ProviderInfo(
            "embeddings",
            settings.embedding_provider.value,
            settings.embedding_model,
            "local" if embeddings_local else "api",
            embeddings_local or settings.embedding_api_key is not None,
        ),
        ProviderInfo(
            "reranking",
            settings.reranker_provider.value,
            settings.reranker_model if reranking_on else "",
            "local" if reranking_on else "off",
            True,
        ),
        ProviderInfo(
            "speech_to_text",
            settings.stt_provider.value,
            settings.stt_model,
            "local" if stt_local else "api",
            stt_local or settings.stt_api_key is not None,
        ),
        ProviderInfo(
            "text_to_speech",
            settings.tts_provider.value,
            settings.tts_voice if tts_local else f"{settings.tts_model}/{settings.tts_voice}",
            "local" if tts_local else "api",
            tts_local or settings.tts_api_key is not None,
        ),
    ]
