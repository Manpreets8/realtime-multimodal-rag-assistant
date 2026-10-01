"""Application configuration.

All tunables (secrets, model names, RAG parameters, limits) are read from the
environment or a `.env` file. Nothing else in the codebase should hard-code
these values - import `get_settings()` instead.
"""

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> project root is three levels above `app/`
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_DIR.parent

_INSECURE_JWT_SECRETS = {"change-me", "dev-only-insecure-jwt-secret-change-me-please"}


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    ANTHROPIC = "anthropic"  # Claude (Messages API): answers, native citations, image understanding


class EmbeddingProviderName(StrEnum):
    LOCAL = "local"  # fastembed (ONNX) running on this machine; no API key needed
    VOYAGE = "voyage"  # Voyage AI hosted API


# Used when EMBEDDING_MODEL / EMBEDDING_DIMENSIONS are left empty.
EMBEDDING_DEFAULTS: dict[EmbeddingProviderName, tuple[str, int]] = {
    EmbeddingProviderName.LOCAL: ("BAAI/bge-small-en-v1.5", 384),
    EmbeddingProviderName.VOYAGE: ("voyage-3.5", 1024),
}


class SpeechProviderName(StrEnum):
    LOCAL = "local"  # faster-whisper on this machine; no API key
    OPENAI = "openai"  # OpenAI transcription API (STT_API_KEY)


STT_DEFAULT_MODELS: dict[SpeechProviderName, str] = {
    SpeechProviderName.LOCAL: "base",
    SpeechProviderName.OPENAI: "whisper-1",
}


class TTSProviderName(StrEnum):
    LOCAL = "local"  # Piper (ONNX) on this machine; no API key
    OPENAI = "openai"  # OpenAI speech API (TTS_API_KEY)


TTS_DEFAULT_VOICES: dict[TTSProviderName, str] = {
    TTSProviderName.LOCAL: "en_US-lessac-medium",
    TTSProviderName.OPENAI: "alloy",
}


class RerankerProviderName(StrEnum):
    LOCAL = "local"  # fastembed cross-encoder on this machine
    NONE = "none"  # keep the retrieval (RRF) order


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files take precedence: backend/.env overrides the root .env.
        env_file=(_PROJECT_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_name: str = "Mindora AI"
    app_tagline: str = "Your knowledge. One intelligent AI."
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    log_json: bool = True
    api_v1_prefix: str = "/api/v1"
    # NoDecode: parse a plain comma-separated string ourselves instead of requiring JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5433/rag_assistant"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_echo: bool = False
    # Server-side limit per SQL statement: a runaway query fails (504) instead of hanging a request.
    db_statement_timeout_ms: int = Field(default=30_000, ge=0, description="0 disables")

    # --- Redis: ingestion jobs, rate limiting, caches, temporary processing state
    redis_url: str = "redis://localhost:6380/0"

    # --- Security --------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-insecure-jwt-secret-change-me-please")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1)

    # --- AI providers ----------------------------------------------------
    # Which implementation serves each capability is chosen here; see app/core/providers.py.
    llm_provider: LLMProviderName = LLMProviderName.ANTHROPIC
    llm_api_key: SecretStr | None = None
    embedding_api_key: SecretStr | None = None
    stt_api_key: SecretStr | None = None
    tts_api_key: SecretStr | None = None
    model_name: str = "claude-opus-5"
    llm_max_tokens: int = Field(default=16000, ge=256, le=128000)
    # Empty = the API default. Lower effort (medium/low) is the main latency and cost lever.
    llm_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    # If the model's safety classifiers decline a request, re-run it on Anthropic's
    # recommended fallback model server-side (beta: server-side-fallback-2026-07-01).
    llm_refusal_fallback: bool = True

    # --- Embeddings --------------------------------------------------------
    embedding_provider: EmbeddingProviderName = EmbeddingProviderName.LOCAL
    embedding_model: str = ""  # empty -> provider default (see EMBEDDING_DEFAULTS)
    # Must match the vector column created by the migrations; checked at startup.
    embedding_dimensions: int = Field(default=0, ge=0)  # 0 -> provider default
    embedding_batch_size: int = Field(default=64, ge=1, le=1000)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    model_cache_dir: Path = _BACKEND_DIR / ".model_cache"

    # --- Ingestion (jobs run in `python -m app.workers.ingestion_worker`) ---
    ingestion_workers: int = Field(default=1, ge=1, le=8, description="Concurrent jobs per worker process")
    # A job whose worker stops sending heartbeats for this long (crashed, killed) is taken over
    # by another worker. Running jobs refresh it, so long documents are not affected.
    ingestion_job_timeout_seconds: int = Field(default=60, ge=5, le=3600)
    # A job that has been started this many times without finishing (it keeps crashing its
    # worker) is moved to the dead-letter stream and the document marked failed.
    ingestion_max_deliveries: int = Field(default=3, ge=1, le=20)
    maintenance_interval_seconds: int = Field(default=300, ge=10, le=86_400)
    # Images uploaded but never sent in a message are deleted after this long.
    orphan_image_ttl_hours: int = Field(default=24, ge=1, le=24 * 30)

    # --- Document insights (AI summaries, key points, topics, entities) -------------
    # Each generation costs LLM tokens, so by default insights are created when a user asks.
    document_insights_auto: bool = False  # true: generate after every successful ingestion
    insights_effort: Literal["low", "medium", "high"] = "low"
    # Text per LLM call; longer documents are analysed in parts, then merged.
    insights_chars_per_call: int = Field(default=60_000, ge=5_000, le=400_000)
    # At most this many parts; beyond that an even sample of the document is analysed
    # (the coverage is stored and shown).
    insights_max_parts: int = Field(default=6, ge=1, le=50)

    # --- Rate limits ("<count>/<window>", window like 30s, 1m, 15m, 1h, 1d) --------
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/15m"  # per client IP + email
    rate_limit_register: str = "10/1h"  # per client IP
    rate_limit_chat: str = "20/1m"  # per user: chat, RAG answers, image questions (LLM spend)
    rate_limit_uploads: str = "60/1h"  # per user: documents and images
    rate_limit_voice: str = "30/1m"  # per user: transcription and speech synthesis

    # --- Caches (Redis) -------------------------------------------------------
    query_embedding_cache_ttl_seconds: int = Field(default=7 * 86_400, ge=0, description="0 disables")

    # --- RAG -------------------------------------------------------------
    top_k: int = Field(default=20, ge=1)
    rerank_top_k: int = Field(default=5, ge=1)
    # Near-duplicate passages (3-word-shingle Jaccard >= this) are dropped before reranking,
    # keeping the better-ranked copy. Empty/None disables.
    dedup_threshold: float | None = Field(default=0.9, gt=0, le=1)
    # Reranked passages scoring below this are not sent to the model. None (the default)
    # disables it: measured on the evaluation set, no threshold removed noise without also
    # removing relevant passages (see evaluation/results/experiment-rerank-threshold.md).
    rerank_min_score: float | None = None
    # Upper bound on the characters of context sent with a question (after RERANK_TOP_K).
    context_max_chars: int = Field(default=12_000, ge=1_000, le=200_000)
    chunk_size: int = Field(default=1000, ge=100)
    chunk_overlap: int = Field(default=150, ge=0)
    # Minimum cosine similarity for a vector-only match. Model-specific: 0.5 was measured
    # for bge-small-en-v1.5 (off-topic queries <= 0.43, relevant >= 0.61). Re-measure if
    # the embedding model changes.
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    # Reranking: the top RERANK_CANDIDATES fused results are re-scored by a cross-encoder
    # and the best RERANK_TOP_K are given to the LLM.
    reranker_provider: RerankerProviderName = RerankerProviderName.LOCAL
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = Field(default=20, ge=1, le=100)

    # --- Chat --------------------------------------------------------------
    # Previous messages (user + assistant) sent with each new question.
    chat_history_messages: int = Field(default=10, ge=0, le=50)
    chat_history_message_chars: int = Field(default=4000, ge=200)
    # Rewrite follow-up questions ("what about the second one?") into standalone
    # search queries using the conversation history (one extra, small LLM call).
    query_rewrite_enabled: bool = True
    query_rewrite_effort: Literal["", "low", "medium", "high"] = "low"

    # --- Uploads ---------------------------------------------------------
    max_file_size: int = Field(default=25 * 1024 * 1024, ge=1, description="Bytes")
    upload_dir: Path = _PROJECT_ROOT / "documents"

    # --- Speech-to-text -----------------------------------------------------
    stt_provider: SpeechProviderName = SpeechProviderName.LOCAL
    stt_model: str = ""  # empty -> provider default (local: base, openai: whisper-1)
    stt_language: str = ""  # ISO 639-1 code to force a language; empty = auto-detect
    max_audio_size: int = Field(default=10 * 1024 * 1024, ge=1, description="Bytes")
    max_audio_seconds: int = Field(default=120, ge=1, le=1800)
    # Transcriptions run at once (local Whisper is CPU-bound; more only slows each one down).
    stt_concurrency: int = Field(default=1, ge=1, le=8)

    # --- Text-to-speech -----------------------------------------------------
    tts_provider: TTSProviderName = TTSProviderName.LOCAL
    tts_voice: str = ""  # empty -> provider default (local: en_US-lessac-medium, openai: alloy)
    tts_model: str = "tts-1"  # openai only
    tts_max_chars: int = Field(
        default=4000, ge=100, le=20_000, description="Longer text is cut at a sentence"
    )
    tts_concurrency: int = Field(default=1, ge=1, le=8)
    tts_cache_ttl_seconds: int = Field(default=86_400, ge=0, description="Redis audio cache; 0 disables")

    # --- Images (vision) ---------------------------------------------------
    max_image_size: int = Field(default=5 * 1024 * 1024, ge=1, description="Bytes per image")
    max_image_dimension: int = Field(default=8000, ge=100, description="Pixels, either side")
    max_images_per_message: int = Field(default=4, ge=1, le=20)
    # Long edge sent to the model. Anthropic recommends <= 1568 px: larger images are
    # downscaled by the API anyway, costing upload time and latency for no benefit.
    image_model_max_edge: int = Field(default=1568, ge=200, le=8000)
    # Images from earlier turns kept in context for follow-ups ("what about the second section?").
    max_history_images: int = Field(default=4, ge=0, le=20)

    # --- Email (welcome email on sign-up) -------------------------------------
    smtp_host: str = ""  # empty disables email
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    # starttls: port 587 (upgrade to TLS after connecting); ssl: port 465; none: local test servers only
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    email_from: str = ""  # e.g. "Mindora AI <you@gmail.com>"; empty -> SMTP_USERNAME
    smtp_timeout_seconds: float = Field(default=20.0, gt=0)
    # Where the app is reached from a user's browser; used for links in emails.
    app_public_url: str = "http://localhost:8080"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        # Allow a comma-separated string in .env: CORS_ORIGINS=http://a,http://b
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator(
        "llm_api_key", "embedding_api_key", "stt_api_key", "tts_api_key", "smtp_password", mode="before"
    )
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        # `LLM_API_KEY=` in .env means "not configured", not an empty key.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_effort", mode="before")
    @classmethod
    def _normalise_effort(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def _empty_dimensions_is_default(cls, value: object) -> object:
        return 0 if isinstance(value, str) and not value.strip() else value

    @field_validator("dedup_threshold", "rerank_min_score", mode="before")
    @classmethod
    def _empty_means_off(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _apply_tts_defaults(self) -> "Settings":
        if not self.tts_voice.strip():
            self.tts_voice = TTS_DEFAULT_VOICES[self.tts_provider]
        return self

    @model_validator(mode="after")
    def _apply_stt_defaults(self) -> "Settings":
        if not self.stt_model.strip():
            self.stt_model = STT_DEFAULT_MODELS[self.stt_provider]
        self.stt_language = self.stt_language.strip().lower()
        return self

    @model_validator(mode="after")
    def _apply_embedding_defaults(self) -> "Settings":
        default_model, default_dimensions = EMBEDDING_DEFAULTS[self.embedding_provider]
        if not self.embedding_model.strip():
            self.embedding_model = default_model
        if self.embedding_dimensions == 0:
            self.embedding_dimensions = default_dimensions
        return self

    @field_validator(
        "rate_limit_login", "rate_limit_register", "rate_limit_chat", "rate_limit_uploads", "rate_limit_voice"
    )
    @classmethod
    def _check_rate_limit(cls, value: str) -> str:
        parse_rate(value)
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.rerank_top_k > self.top_k:
            raise ValueError("RERANK_TOP_K must not exceed TOP_K")
        if self.rerank_top_k > self.rerank_candidates:
            raise ValueError("RERANK_TOP_K must not exceed RERANK_CANDIDATES")
        if self.email_enabled and not self.email_sender:
            raise ValueError("Set EMAIL_FROM (or SMTP_USERNAME) when SMTP_HOST is set")
        if self.environment is Environment.PRODUCTION:
            secret = self.jwt_secret.get_secret_value()
            if secret in _INSECURE_JWT_SECRETS or len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a random value of at least 32 characters in production"
                )
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS_ORIGINS is not allowed in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host.strip())

    @property
    def email_sender(self) -> str:
        return self.email_from.strip() or self.smtp_username.strip()


_RATE = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*([smhd])\s*$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


def parse_rate(value: str) -> tuple[int, int]:
    """ "20/1m" -> (20 requests, 60 seconds)."""
    match = _RATE.match(value)
    if not match or int(match.group(1)) < 1:
        raise ValueError(f"Invalid rate limit {value!r}; use '<count>/<window>', e.g. '20/1m' or '10/15m'")
    count, amount, unit = match.groups()
    return int(count), int(amount or 1) * _UNIT_SECONDS[unit]


@lru_cache
def get_settings() -> Settings:
    return Settings()
