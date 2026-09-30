"""Selects the LLM provider from LLM_PROVIDER. The rest of the app only sees `LLMProvider`."""

from functools import lru_cache
from typing import cast

from app.core.ai_calls import AICallKind, InstrumentedProvider, llm_usage
from app.core.config import LLMProviderName, Settings, get_settings
from app.llm.base import LLMProvider, VisionNotSupportedError
from app.llm.claude import create_claude_provider


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Raises LLMNotConfiguredError when the provider's credentials are missing."""
    if settings.llm_provider is LLMProviderName.ANTHROPIC:
        provider = create_claude_provider(settings)
    else:  # pragma: no cover - the settings enum admits no other value
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
    wrapped = InstrumentedProvider(
        provider,
        kind=AICallKind.LLM,
        provider=provider.provider_name,
        model=provider.model_name,
        methods={"generate": llm_usage},
    )
    return cast(LLMProvider, wrapped)  # a transparent wrapper around the provider


@lru_cache
def get_llm_provider() -> LLMProvider:
    return build_llm_provider(get_settings())


def ensure_vision(llm: LLMProvider) -> None:
    """Fail with a clear error before sending images to a provider that cannot read them."""
    if not llm.supports_images:
        raise VisionNotSupportedError()
