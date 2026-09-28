import io
import json
import math
import re
import struct
import wave
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.core.errors import FileTooLargeError, UnsupportedFileTypeError
from app.multimodal import speech
from app.multimodal.speech import (
    SAMPLE_RATE,
    AudioTooLongError,
    InvalidAudioError,
    NoSpeechError,
    OpenAITranscriptionProvider,
    SpeechNotConfiguredError,
    SpeechToTextError,
    build_speech_provider,
    decode_audio,
)
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedSpeech
from tests.images import PNG

FIXTURE = Path(__file__).parent / "fixtures" / "spoken_question.webm"
LIMITS = {"max_bytes": 10 * 1024 * 1024, "max_seconds": 120}
TRANSCRIBE = "/api/v1/voice/transcribe"


def wav(seconds: float, *, frequency: float = 440.0, rate: int = 44_100, silent: bool = False) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            value = 0 if silent else int(8000 * math.sin(2 * math.pi * frequency * i / rate))
            frames += struct.pack("<hh", value, value)
        out.writeframes(bytes(frames))
    return buffer.getvalue()


# --- decoding ---------------------------------------------------------------------------


def test_decodes_to_16khz_mono_float() -> None:
    audio = decode_audio(wav(1.5), **LIMITS)  # 44.1 kHz stereo in

    assert audio.dtype.name == "float32"
    assert abs(audio.size - 1.5 * SAMPLE_RATE) <= SAMPLE_RATE * 0.01
    assert float(abs(audio).max()) <= 1.0


def test_browser_webm_opus_recording_decodes() -> None:
    audio = decode_audio(FIXTURE.read_bytes(), **LIMITS)

    assert 6.4 < audio.size / SAMPLE_RATE < 6.8


@pytest.mark.parametrize(
    ("data", "limits", "error"),
    [
        pytest.param(b"", LIMITS, InvalidAudioError, id="empty"),
        pytest.param(b"this is not audio at all" * 10, LIMITS, InvalidAudioError, id="garbage"),
        pytest.param(PNG, LIMITS, UnsupportedFileTypeError, id="image-no-audio-stream"),
        pytest.param(wav(0.1), LIMITS, NoSpeechError, id="too-short"),
        pytest.param(wav(2.0), {"max_bytes": 10**7, "max_seconds": 1}, AudioTooLongError, id="too-long"),
        pytest.param(wav(1.0), {"max_bytes": 1000, "max_seconds": 120}, FileTooLargeError, id="too-large"),
    ],
)
def test_invalid_audio_is_rejected(data: bytes, limits: dict, error: type[Exception]) -> None:
    with pytest.raises(error):
        decode_audio(data, **limits)


# --- providers ----------------------------------------------------------------------------


def test_openai_provider_requires_a_key() -> None:
    with pytest.raises(SpeechNotConfiguredError):
        build_speech_provider(Settings(_env_file=None, stt_provider="openai"))
    assert build_speech_provider(Settings(_env_file=None)).model_name == "base"


async def test_openai_provider_uploads_the_original_file() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " Hello there. "})

    provider = OpenAITranscriptionProvider(
        "sk-test", "whisper-1", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    audio = decode_audio(wav(1.0), **LIMITS)

    transcript = await provider.transcribe(audio, data=b"RIFF-bytes", filename="clip.wav", language="en")

    assert transcript.text == "Hello there."
    assert transcript.model == "openai/whisper-1"
    body = seen[0].content
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    assert b'name="model"\r\n\r\nwhisper-1' in body
    assert b'name="language"\r\n\r\nen' in body
    assert b'filename="clip.wav"' in body and b"RIFF-bytes" in body


@pytest.mark.parametrize(("status", "message"), [(401, "rejected the API key"), (500, "returned an error")])
async def test_openai_provider_errors(status: int, message: str) -> None:
    provider = OpenAITranscriptionProvider(
        "sk-test",
        "whisper-1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={}))),
    )

    with pytest.raises(SpeechToTextError, match=message):
        await provider.transcribe(
            decode_audio(wav(1.0), **LIMITS), data=b"x", filename="a.wav", language=None
        )


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.mark.integration
async def test_transcribe_endpoint(client: AsyncClient, alice: dict, stt: ScriptedSpeech) -> None:
    stt.text = "How much annual leave do I get?"

    response = await client.post(
        TRANSCRIBE,
        files={"file": ("rec.webm", FIXTURE.read_bytes(), "audio/webm")},
        data={"language": "EN"},
        headers=alice,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "How much annual leave do I get?"
    assert body["language"] == "en" and body["model"] == "test-stt"
    assert 6.4 < body["duration_seconds"] < 6.8
    assert body["processing_ms"] > 0
    assert stt.calls[0]["language"] == "en" and stt.calls[0]["filename"] == "rec.webm"


@pytest.mark.integration
async def test_transcribe_ignores_the_claimed_content_type(client: AsyncClient, alice: dict) -> None:
    response = await client.post(TRANSCRIBE, files={"file": ("voice.webm", PNG, "audio/webm")}, headers=alice)

    assert response.status_code == 415


@pytest.mark.integration
@pytest.mark.parametrize(
    ("data", "status", "code"),
    [(b"not audio", 422, "invalid_audio"), (b"", 422, "invalid_audio")],
    ids=["garbage", "empty"],
)
async def test_transcribe_rejects_bad_uploads(
    client: AsyncClient, alice: dict, data: bytes, status: int, code: str
) -> None:
    response = await client.post(TRANSCRIBE, files={"file": ("a.webm", data)}, headers=alice)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.integration
async def test_nothing_said_is_a_clear_error(client: AsyncClient, alice: dict, stt: ScriptedSpeech) -> None:
    stt.text = ""

    response = await client.post(TRANSCRIBE, files={"file": ("a.wav", wav(1.0))}, headers=alice)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_speech"


@pytest.mark.integration
async def test_size_limit_and_auth(client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_audio_size", 1000)

    too_big = await client.post(TRANSCRIBE, files={"file": ("a.wav", wav(1.0))}, headers=alice)
    anonymous = await client.post(TRANSCRIBE, files={"file": ("a.wav", wav(1.0))})

    assert too_big.status_code == 413
    assert anonymous.status_code == 401


# --- real model ------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("-", " "))


@pytest.mark.model
@pytest.mark.integration
async def test_real_whisper_transcribes_speech_and_stays_silent_on_silence(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = build_speech_provider(get_settings())
    monkeypatch.setattr(speech, "get_speech_provider", lambda: provider)

    spoken = await client.post(TRANSCRIBE, files={"file": ("q.webm", FIXTURE.read_bytes())}, headers=alice)
    silent = await client.post(TRANSCRIBE, files={"file": ("s.wav", wav(3.0, silent=True))}, headers=alice)

    assert spoken.status_code == 200, spoken.text
    expected = "how many days of annual leave do full time employees get and can unused days be carried over"
    assert _words(spoken.json()["text"]) == expected.split()
    assert spoken.json()["language"] == "en"
    # Voice-activity detection: no invented words for silence.
    assert silent.status_code == 422
    assert silent.json()["error"]["code"] == "no_speech"
    assert json.loads(silent.text)["error"]["message"].startswith("No speech was detected")
