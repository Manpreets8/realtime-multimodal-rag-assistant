"""Text-to-speech for assistant answers.

Providers (TTS_PROVIDER):
- `local`: Piper neural TTS (ONNX Runtime on CPU, no API key). Measured here about
  20x faster than real time once loaded; the voice (~63 MB) downloads once.
- `openai`: OpenAI's speech API.

Output is MP3 (plays in every browser, about 5x smaller than WAV). Answers are
cleaned for speech first (markdown, citation markers and URLs removed) and cut at
a sentence boundary past TTS_MAX_CHARS. A Redis cache (shared by all API processes,
TTS_CACHE_TTL_SECONDS) makes re-listening free.
"""

import asyncio
import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, cast

import av
import httpx
import numpy as np
from starlette.concurrency import run_in_threadpool

from app.core import cache
from app.core.ai_calls import AICallKind, InstrumentedProvider, synthesis_usage
from app.core.config import Settings, TTSProviderName, get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

MP3_BITRATE = 64_000


class NothingToSpeakError(AppError):
    status_code = 422
    code = "nothing_to_speak"
    message = "There is no text to read aloud."


class TextToSpeechError(AppError):
    status_code = 502
    code = "tts_error"
    message = "Speech could not be generated. Please try again."


class TTSNotConfiguredError(RuntimeError):
    """Invalid TTS configuration; raised at startup."""


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    data: bytes
    media_type: str
    voice: str
    characters: int
    truncated: bool


# --- text preparation --------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_CITATION = re.compile(r"\[\d+(?:\s*[,-]\s*\d+)*\]")
# `_` only counts as emphasis at word edges, so snake_case_names survive.
_EMPHASIS = re.compile(r"(\*\*|\*|~~|(?<!\w)__|(?<!\w)_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def prepare_speech_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Plain, speakable prose: (text, truncated). Markdown and citation markers are removed,
    list items become sentences, and long text is cut at a sentence boundary."""
    text = _CODE_BLOCK.sub(" (code omitted) ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("link", text)
    text = _CITATION.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    # Line breaks become pauses: end each line as a sentence if it doesn't already.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " ".join(line if line[-1] in ".!?:;," else f"{line}." for line in lines)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)  # "leave [1]." left "leave ."

    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
    cut = cut[: ends[-1]] if ends and ends[-1] > max_chars // 2 else cut.rsplit(" ", 1)[0]
    return cut.strip(), True


def encode_mp3(samples: np.ndarray, sample_rate: int) -> bytes:
    """Mono int16 PCM -> MP3 bytes. Blocking."""
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=sample_rate)
        stream.layout = "mono"
        stream.bit_rate = MP3_BITRATE
        fifo = av.AudioFifo()
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        fifo.write(frame)
        frame_size = stream.codec_context.frame_size or 1152
        while fifo.samples >= frame_size:
            for packet in stream.encode(fifo.read(frame_size)):
                container.mux(packet)
        if fifo.samples:
            for packet in stream.encode(fifo.read()):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


# --- providers ----------------------------------------------------------------------


class TTSProvider(Protocol):
    voice: str

    async def synthesize(self, text: str) -> bytes: ...


class LocalPiperProvider:
    def __init__(self, voice: str, cache_dir: Path) -> None:
        self.voice = voice
        self._cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _load(self):  # piper is imported lazily
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from piper import PiperVoice
                    from piper.download_voices import download_voice

                    started = time.perf_counter()
                    try:
                        model_path = self._cache_dir / f"{self.voice}.onnx"
                        if not model_path.exists():
                            self._cache_dir.mkdir(parents=True, exist_ok=True)
                            download_voice(self.voice, self._cache_dir)
                        self._model = PiperVoice.load(model_path)
                    except Exception as exc:
                        logger.exception("tts_voice_load_failed", extra={"voice": self.voice})
                        raise TextToSpeechError("The speech voice could not be loaded.") from exc
                    logger.info(
                        "tts_voice_loaded",
                        extra={"voice": self.voice, "load_ms": round((time.perf_counter() - started) * 1000)},
                    )
        return self._model

    def warm_up(self) -> None:
        voice = self._load()
        for _ in voice.synthesize("Ready."):  # initialises the ONNX session
            pass

    def _synthesize_sync(self, text: str) -> bytes:
        voice = self._load()
        chunks = list(voice.synthesize(text))
        if not chunks:
            raise NothingToSpeakError()
        samples = np.concatenate([chunk.audio_int16_array for chunk in chunks])
        return encode_mp3(samples, chunks[0].sample_rate)

    async def synthesize(self, text: str) -> bytes:
        try:
            return await run_in_threadpool(self._synthesize_sync, text)
        except AppError:
            raise
        except Exception as exc:
            logger.exception("tts_local_failed")
            raise TextToSpeechError() from exc


class OpenAISpeechProvider:
    URL = "https://api.openai.com/v1/audio/speech"

    def __init__(self, api_key: str, model: str, voice: str, client: httpx.AsyncClient | None = None) -> None:
        self.voice = voice
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=60.0)

    async def synthesize(self, text: str) -> bytes:
        try:
            response = await self._client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "voice": self.voice, "input": text, "response_format": "mp3"},
            )
        except httpx.HTTPError as exc:
            raise TextToSpeechError("The speech service could not be reached.") from exc
        if response.status_code >= 400:
            logger.error(
                "tts_api_error", extra={"status_code": response.status_code, "body": response.text[:300]}
            )
            if response.status_code in (401, 403):
                raise TextToSpeechError("The speech service rejected the API key.")
            raise TextToSpeechError(f"The speech service returned an error ({response.status_code}).")
        return response.content


def build_tts_provider(settings: Settings) -> TTSProvider:
    if settings.tts_provider is TTSProviderName.OPENAI:
        if settings.tts_api_key is None:
            raise TTSNotConfiguredError("TTS_PROVIDER=openai requires TTS_API_KEY to be set.")
        return OpenAISpeechProvider(
            settings.tts_api_key.get_secret_value(), settings.tts_model, settings.tts_voice
        )
    return LocalPiperProvider(settings.tts_voice, settings.model_cache_dir / "piper")


@lru_cache
def get_tts_provider() -> TTSProvider:
    settings = get_settings()
    provider = build_tts_provider(settings)
    local = settings.tts_provider is TTSProviderName.LOCAL
    wrapped = InstrumentedProvider(
        provider,
        kind=AICallKind.TEXT_TO_SPEECH,
        provider=settings.tts_provider.value,
        model=provider.voice if local else f"{settings.tts_model}/{provider.voice}",
        methods={"synthesize": synthesis_usage},
    )
    return cast(TTSProvider, wrapped)


# --- service ------------------------------------------------------------------------


@lru_cache
def _limiter() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().tts_concurrency)


async def synthesize(text: str) -> tuple[SpeechAudio, bool, float]:
    """Return (audio, cache_hit, elapsed_ms)."""
    settings = get_settings()
    started = time.perf_counter()
    speech_text, truncated = prepare_speech_text(text, settings.tts_max_chars)
    if not re.search(r"\w", speech_text):
        raise NothingToSpeakError()

    provider = get_tts_provider()
    key = cache.cache_key("tts", type(provider).__name__, provider.voice, speech_text)
    cached = await cache.get_bytes(key)
    if cached is None:
        async with _limiter():
            cached = await provider.synthesize(speech_text)
        await cache.set_bytes(key, cached, settings.tts_cache_ttl_seconds)
        hit = False
    else:
        hit = True

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "tts_completed",
        extra={
            "voice": provider.voice,
            "characters": len(speech_text),
            "bytes": len(cached),
            "cache_hit": hit,
            "tts_ms": elapsed,
        },
    )
    audio = SpeechAudio(cached, "audio/mpeg", provider.voice, len(speech_text), truncated)
    return audio, hit, elapsed
