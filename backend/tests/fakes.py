"""Test doubles. Used only by tests; the application never falls back to these."""

import asyncio
import hashlib
import math
import re
import uuid
from collections.abc import Sequence

from app.models import EMBEDDING_COLUMN_DIMENSIONS

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbeddingProvider:
    """Deterministic bag-of-words embeddings: texts sharing words get similar vectors,
    so retrieval behaviour is meaningful in tests without loading a real model."""

    model_name = "test-hashing-embedder"
    dimensions = EMBEDDING_COLUMN_DIMENSIONS

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []
        self.fail_with: Exception | None = None

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _TOKEN.findall(text.lower()):
            bucket = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % self.dimensions
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail_with:
            raise self.fail_with
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail_with:
            raise self.fail_with
        return self._vector(text)


class RecordingQueue:
    """Stands in for the Redis JobQueue in API tests: records instead of queueing,
    so each test decides when (and whether) ingestion runs."""

    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue(self, document_id: uuid.UUID) -> bool:
        self.enqueued.append(document_id)
        return True


class ScriptedLLM:
    """Test stand-in for ClaudeClient: records requests and returns scripted output.

    Grounded/general answer calls return `answer` with `cite` as citations (each a
    (document_index, cited_text) pair spanning the whole answer). Query-rewrite calls,
    recognised by their system prompt, return `rewrite`."""

    model_name = "test-llm"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.answer = "Scripted answer."
        self.cite: list[tuple[int, str]] = []
        self.fail_with: Exception | None = None
        self.rewrite = "rewritten standalone query"
        self.rewrite_fail_with: Exception | None = None
        # Streaming: text streamed and then discarded, as when a model declines mid-stream.
        self.declined_prefix: str | None = None
        # When set, streaming pauses after the first chunk until the event is set.
        self.gate: asyncio.Event | None = None

    @staticmethod
    def _is_rewrite(call: dict) -> bool:
        from app.rag.conversation import QUERY_REWRITE_SYSTEM_PROMPT

        return call["system"] == QUERY_REWRITE_SYSTEM_PROMPT

    @property
    def answer_calls(self) -> list[dict]:
        return [call for call in self.calls if not self._is_rewrite(call)]

    @property
    def rewrite_calls(self) -> list[dict]:
        return [call for call in self.calls if self._is_rewrite(call)]

    async def generate(self, *, system: str, messages: list[dict], max_tokens=None, effort=None, stream=None):
        from app.llm.claude import CitationSpan, LLMResponse

        call = {"system": system, "messages": messages, "max_tokens": max_tokens, "effort": effort}
        self.calls.append(call)
        if self._is_rewrite(call):
            if self.rewrite_fail_with:
                raise self.rewrite_fail_with
            return LLMResponse(self.rewrite, [], self.model_name, "end_turn", 30, 8, 1.0)
        if self.fail_with:
            raise self.fail_with
        if stream is not None:
            if self.declined_prefix:
                await stream.text(self.declined_prefix)
                await stream.restart()
            for number, chunk in enumerate(re.findall(r"\S+\s*", self.answer)):
                await stream.text(chunk)
                if number == 0 and self.gate is not None:
                    await self.gate.wait()
        spans = [CitationSpan(index, text, 0, len(self.answer)) for index, text in self.cite]
        return LLMResponse(
            text=self.answer,
            citations=spans,
            model=self.model_name,
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=20,
            latency_ms=1.0,
        )


class ScriptedSpeech:
    """Test stand-in for the speech provider: returns `text` for any audio."""

    model_name = "test-stt"

    def __init__(self) -> None:
        self.text = "Transcribed words."
        self.calls: list[dict] = []

    async def transcribe(self, audio, *, data: bytes, filename: str, language: str | None):
        from app.multimodal.speech import SAMPLE_RATE, Transcript

        self.calls.append({"samples": audio.size, "filename": filename, "language": language})
        return Transcript(
            self.text, language or "en", 0.99, round(audio.size / SAMPLE_RATE, 2), self.model_name
        )


class ScriptedTTS:
    """Text-to-speech stand-in: records the (already cleaned) text and returns fake MP3 bytes."""

    voice = "test-voice"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return b"ID3fake-mp3:" + text.encode()
