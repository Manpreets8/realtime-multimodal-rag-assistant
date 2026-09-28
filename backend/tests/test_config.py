import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides: object) -> Settings:
    # _env_file=None isolates tests from any developer .env file.
    return Settings(_env_file=None, **overrides)


def test_defaults_are_consistent() -> None:
    settings = make_settings()

    assert settings.chunk_overlap < settings.chunk_size
    assert settings.rerank_top_k <= settings.top_k


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        make_settings(chunk_size=500, chunk_overlap=500)


def test_rerank_top_k_cannot_exceed_top_k() -> None:
    with pytest.raises(ValidationError, match="RERANK_TOP_K"):
        make_settings(top_k=5, rerank_top_k=10)


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(environment="production")


def test_production_accepts_strong_secret() -> None:
    settings = make_settings(environment="production", jwt_secret="x" * 48)

    assert settings.is_production


def test_cors_origins_accepts_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    assert make_settings().cors_origins == ["http://a.test", "http://b.test"]


def test_secrets_are_not_exposed_in_repr() -> None:
    settings = make_settings(llm_api_key="sk-secret-value")

    assert "sk-secret-value" not in repr(settings)
