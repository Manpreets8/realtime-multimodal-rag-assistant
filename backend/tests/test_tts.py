import re

import httpx
import numpy as np
import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.multimodal import speech, tts
from app.multimodal.speech import decode_audio
from app.multimodal.tts import (
    LocalPiperProvider,
    OpenAISpeechProvider,
    TextToSpeechError,
    TTSNotConfiguredError,
    build_tts_provider,
    encode_mp3,
    prepare_speech_text,
)
from tests.conftest import RegisterFn, bearer
from tests.fakes import ScriptedTTS

SYNTHESIZE = "/api/v1/voice/synthesize"


# --- text preparation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Employees get **25 days** of leave [1].", "Employees get 25 days of leave."),
        ("Leave carries over [1, 2] and expires [3-4].", "Leave carries over and expires."),
        ("## Summary\n- First point\n- Second point [2]", "Summary. First point. Second point."),
        ("1. Open settings\n2) Click *Save*", "Open settings. Click Save."),
        ("See [the policy](https://example.com/p) or https://x.io/a?b=1 now.", "See the policy or link now."),
        ("Run `make test`:\n```bash\nrm -rf /\n```\nDone.", "Run make test: (code omitted). Done."),
        ("snake_case_name stays", "snake_case_name stays."),
    ],
)
def test_prepare_speech_text(raw: str, spoken: str) -> None:
    assert prepare_speech_text(raw, 4000) == (spoken, False)


def test_long_text_is_cut_at_a_sentence_boundary() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(100))

    spoken, truncated = prepare_speech_text(text, 200)

    assert truncated is True
    assert len(spoken) <= 200
    assert spoken.endswith("is here.")


def test_long_text_without_sentences_is_cut_at_a_word() -> None:
    spoken, truncated = prepare_speech_text("word " * 100, 101)

    assert truncated and spoken.endswith("word") and len(spoken) <= 101


def test_encode_mp3_produces_decodable_audio() -> None:
    rate = 22_050
    tone = (8000 * np.sin(2 * np.pi * 440 * np.arange(rate * 2) / rate)).astype(np.int16)

    data = encode_mp3(tone, rate)

    decoded = decode_audio(data, max_bytes=10_000_000, max_seconds=60)
    assert 1.9 < decoded.size / speech.SAMPLE_RATE < 2.2
    assert len(data) < 20_000  # ~64 kbps


# --- providers ------------------------------------------------------------------------------


def test_provider_selection() -> None:
    with pytest.raises(TTSNotConfiguredError):
        build_tts_provider(Settings(_env_file=None, tts_provider="openai"))
    local = build_tts_provider(Settings(_env_file=None))
    assert isinstance(local, LocalPiperProvider) and local.voice == "en_US-lessac-medium"
    remote = build_tts_provider(Settings(_env_file=None, tts_provider="openai", tts_api_key="sk-test"))
    assert isinstance(remote, OpenAISpeechProvider) and remote.voice == "alloy"


async def test_openai_provider_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3-audio")

    provider = OpenAISpeechProvider(
        "sk-test", "tts-1", "nova", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    assert await provider.synthesize("Hello.") == b"ID3-audio"
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    assert seen[0].read() == b'{"model":"tts-1","voice":"nova","input":"Hello.","response_format":"mp3"}'


@pytest.mark.parametrize(("status", "message"), [(401, "rejected the API key"), (500, "returned an error")])
async def test_openai_provider_errors(status: int, message: str) -> None:
    provider = OpenAISpeechProvider(
        "sk-test",
        "tts-1",
        "alloy",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={}))),
    )

    with pytest.raises(TextToSpeechError, match=message):
        await provider.synthesize("Hello.")


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.mark.integration
async def test_synthesize_speaks_the_cleaned_answer(
    client: AsyncClient, alice: dict, tts_provider: ScriptedTTS
) -> None:
    response = await client.post(SYNTHESIZE, json={"text": "You get **25 days** [1]."}, headers=alice)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3fake-mp3:You get 25 days."
    assert response.headers["x-speech-voice"] == "test-voice"
    assert response.headers["x-speech-truncated"] == "false"
    assert response.headers["x-speech-cache"] == "miss"
    assert response.headers["cache-control"] == "private, no-store"
    assert tts_provider.calls == ["You get 25 days."]


@pytest.mark.integration
async def test_repeat_requests_are_served_from_the_cache(
    client: AsyncClient, alice: dict, tts_provider: ScriptedTTS
) -> None:
    first = await client.post(SYNTHESIZE, json={"text": "Replay me."}, headers=alice)
    # Same spoken text after cleaning -> same audio, no second synthesis.
    second = await client.post(SYNTHESIZE, json={"text": "Replay me. [2]"}, headers=alice)

    assert first.content == second.content
    assert second.headers["x-speech-cache"] == "hit"
    assert tts_provider.calls == ["Replay me."]


@pytest.mark.integration
async def test_long_text_is_truncated(
    client: AsyncClient, alice: dict, tts_provider: ScriptedTTS, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "tts_max_chars", 100)

    response = await client.post(SYNTHESIZE, json={"text": "This is a sentence. " * 20}, headers=alice)

    assert response.headers["x-speech-truncated"] == "true"
    assert len(tts_provider.calls[0]) <= 100


@pytest.mark.integration
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"text": "[1] [2]"}, "nothing_to_speak"),
        ({"text": "```\ncode\n```"}, None),
        ({"text": ""}, "validation_error"),
    ],
    ids=["only-markers", "only-code", "empty"],
)
async def test_nothing_to_speak(
    client: AsyncClient, alice: dict, tts_provider: ScriptedTTS, body: dict, code: str | None
) -> None:
    response = await client.post(SYNTHESIZE, json=body, headers=alice)

    if code is None:  # a code-only answer is still announced
        assert response.status_code == 200
        assert tts_provider.calls == ["(code omitted)."]
        return
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert tts_provider.calls == []


@pytest.mark.integration
async def test_provider_failure_is_a_502_without_internals(
    client: AsyncClient, alice: dict, tts_provider: ScriptedTTS
) -> None:
    tts_provider.error = TextToSpeechError()

    response = await client.post(SYNTHESIZE, json={"text": "Hello."}, headers=alice)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "tts_error"


@pytest.mark.integration
async def test_synthesize_requires_auth(client: AsyncClient) -> None:
    assert (await client.post(SYNTHESIZE, json={"text": "Hello."})).status_code == 401


# --- real model ------------------------------------------------------------------------------


@pytest.mark.model
@pytest.mark.integration
async def test_real_piper_speech_round_trips_through_whisper(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tts, "get_tts_provider", lambda: build_tts_provider(get_settings()))
    whisper = speech.build_speech_provider(get_settings())
    answer = "Full-time employees receive **twenty days** of annual leave each year [1]."

    response = await client.post(SYNTHESIZE, json={"text": answer}, headers=alice)

    assert response.status_code == 200, response.text
    assert response.content[:3] == b"ID3" or response.content[:2] in (b"\xff\xfb", b"\xff\xf3")
    audio = decode_audio(response.content, max_bytes=10_000_000, max_seconds=60)
    transcript = await whisper.transcribe(audio, data=response.content, filename="a.mp3", language="en")
    words = re.findall(r"[a-z0-9]+", transcript.text.lower().replace("-", " ").replace("20", "twenty"))
    expected = "full time employees receive twenty days of annual leave each year"
    assert words == expected.split()
