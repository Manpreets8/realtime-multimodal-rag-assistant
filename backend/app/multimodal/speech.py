"""Speech-to-text.

Audio is identified by decoding it with PyAV (FFmpeg), never by the client's
Content-Type, so browser recordings (WebM/Ogg Opus, MP4/AAC) and WAV/MP3/M4A all
work and non-audio files are rejected. Decoding stops as soon as the audio passes
MAX_AUDIO_SECONDS, so a small but very long file can't tie up the CPU.

Providers (STT_PROVIDER):
- `local`: faster-whisper (CTranslate2, int8 on CPU). Voice-activity detection is
  always on: without it Whisper invents text for silence (measured: `base`
  transcribed three seconds of silence as "You").
- `openai`: OpenAI's transcription API.
"""

import asyncio
import io
import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import av
import httpx
import numpy as np
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, SpeechProviderName, get_settings
from app.core.errors import AppError, FileTooLargeError, UnsupportedFileTypeError

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # what Whisper expects
MIN_SPEECH_SECONDS = 0.3


class InvalidAudioError(AppError):
    status_code = 422
    code = "invalid_audio"
    message = "The recording could not be read as audio."


class AudioTooLongError(AppError):
    status_code = 422
    code = "audio_too_long"
    message = "The recording is too long."


class NoSpeechError(AppError):
    status_code = 422
    code = "no_speech"
    message = "No speech was detected. Check your microphone and try again."


class SpeechToTextError(AppError):
    status_code = 502
    code = "stt_error"
    message = "Speech recognition failed. Please try again."


class SpeechNotConfiguredError(RuntimeError):
    """Invalid STT configuration; raised at startup."""


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float
    model: str


def decode_audio(data: bytes, *, max_bytes: int, max_seconds: int) -> np.ndarray:
    """Decode any FFmpeg-readable audio to 16 kHz mono float32. Blocking: call from a thread."""
    if not data:
        raise InvalidAudioError("The recording is empty.")
    if len(data) > max_bytes:
        raise FileTooLargeError(f"Recordings must be at most {max_bytes // (1024 * 1024)} MB.")
    max_samples = max_seconds * SAMPLE_RATE
    chunks: list[np.ndarray] = []
    total = 0
    try:
        with av.open(io.BytesIO(data), mode="r", metadata_errors="ignore") as container:
            if not container.streams.audio:
                raise UnsupportedFileTypeError("The file contains no audio.")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    samples = resampled.to_ndarray().reshape(-1)
                    total += samples.size
                    if total > max_samples:
                        raise AudioTooLongError(f"Recordings must be at most {max_seconds} seconds long.")
                    chunks.append(samples)
            for resampled in resampler.resample(None):  # flush
                chunks.append(resampled.to_ndarray().reshape(-1))
    except (AudioTooLongError, UnsupportedFileTypeError):
        raise
    except (av.error.FFmpegError, ValueError, IndexError) as exc:
        raise InvalidAudioError("The file is not a supported audio recording or is damaged.") from exc

    audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)
    if audio.size < MIN_SPEECH_SECONDS * SAMPLE_RATE:
        raise NoSpeechError("The recording is too short.")
    return audio


class SpeechProvider(Protocol):
    model_name: str

    async def transcribe(
        self, audio: np.ndarray, *, data: bytes, filename: str, language: str | None
    ) -> Transcript: ...


class LocalWhisperProvider:
    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    started = time.perf_counter()
                    try:
                        self._model = WhisperModel(
                            self.model_name,
                            device="cpu",
                            compute_type="int8",
                            download_root=str(self._cache_dir),
                        )
                    except Exception as exc:
                        logger.exception("stt_model_load_failed", extra={"model": self.model_name})
                        raise SpeechToTextError("The speech recognition model could not be loaded.") from exc
                    logger.info(
                        "stt_model_loaded",
                        extra={
                            "model": self.model_name,
                            "load_ms": round((time.perf_counter() - started) * 1000),
                        },
                    )
        return self._model

    def warm_up(self) -> None:
        self._load()

    def _transcribe_sync(self, audio: np.ndarray, language: str | None) -> Transcript:
        model = self._load()
        segments, info = model.transcribe(
            audio,
            language=language or None,
            beam_size=5,
            vad_filter=True,  # never transcribe silence (Whisper hallucinates on it)
            condition_on_previous_text=False,  # avoids repetition loops on long audio
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()  # segments are lazy
        return Transcript(
            text=text,
            language=info.language,
            language_probability=round(float(info.language_probability), 3),
            duration_seconds=round(audio.size / SAMPLE_RATE, 2),
            model=f"faster-whisper/{self.model_name}",
        )

    async def transcribe(
        self, audio: np.ndarray, *, data: bytes, filename: str, language: str | None
    ) -> Transcript:
        try:
            return await run_in_threadpool(self._transcribe_sync, audio, language)
        except SpeechToTextError:
            raise
        except Exception as exc:
            logger.exception("stt_local_failed")
            raise SpeechToTextError() from exc


class OpenAITranscriptionProvider:
    """OpenAI audio transcription REST API. The original file is uploaded as-is."""

    URL = "https://api.openai.com/v1/audio/transcriptions"

    def __init__(
        self, api_key: str, model_name: str, timeout: float = 60.0, client: httpx.AsyncClient | None = None
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def transcribe(
        self, audio: np.ndarray, *, data: bytes, filename: str, language: str | None
    ) -> Transcript:
        form = {"model": self.model_name, "response_format": "json"}
        if language:
            form["language"] = language
        try:
            response = await self._client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                data=form,
                files={"file": (filename, data)},
            )
        except httpx.HTTPError as exc:
            raise SpeechToTextError("The speech recognition service could not be reached.") from exc
        if response.status_code >= 400:
            logger.error(
                "stt_api_error", extra={"status_code": response.status_code, "body": response.text[:300]}
            )
            if response.status_code in (401, 403):
                raise SpeechToTextError("The speech recognition service rejected the API key.")
            raise SpeechToTextError(
                f"The speech recognition service returned an error ({response.status_code})."
            )
        return Transcript(
            text=str(response.json().get("text", "")).strip(),
            language=language,
            language_probability=None,
            duration_seconds=round(audio.size / SAMPLE_RATE, 2),
            model=f"openai/{self.model_name}",
        )


def build_speech_provider(settings: Settings) -> SpeechProvider:
    if settings.stt_provider is SpeechProviderName.OPENAI:
        if settings.stt_api_key is None:
            raise SpeechNotConfiguredError("STT_PROVIDER=openai requires STT_API_KEY to be set.")
        return OpenAITranscriptionProvider(settings.stt_api_key.get_secret_value(), settings.stt_model)
    return LocalWhisperProvider(settings.stt_model, settings.model_cache_dir / "whisper")


@lru_cache
def get_speech_provider() -> SpeechProvider:
    return build_speech_provider(get_settings())


@lru_cache
def _limiter() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().stt_concurrency)


async def transcribe_upload(
    data: bytes, filename: str, language: str | None = None
) -> tuple[Transcript, float]:
    """Validate, decode and transcribe; returns (transcript, processing_ms).

    Raises NoSpeechError when nothing was said."""
    settings = get_settings()
    started = time.perf_counter()
    audio = await run_in_threadpool(
        decode_audio, data, max_bytes=settings.max_audio_size, max_seconds=settings.max_audio_seconds
    )
    async with _limiter():
        transcript = await get_speech_provider().transcribe(
            audio, data=data, filename=filename, language=language or settings.stt_language or None
        )
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "stt_completed",
        extra={
            "model": transcript.model,
            "audio_seconds": transcript.duration_seconds,
            "characters": len(transcript.text),
            "language": transcript.language,
            "stt_ms": elapsed,
        },
    )
    if not transcript.text:
        raise NoSpeechError()
    return transcript, elapsed
