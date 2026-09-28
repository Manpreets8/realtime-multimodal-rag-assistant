from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, VoiceLimit
from app.core.config import get_settings
from app.core.errors import FileTooLargeError
from app.multimodal import speech, tts

router = APIRouter(prefix="/voice", tags=["voice"])


class TranscriptionResponse(BaseModel):
    text: str
    language: str | None = Field(description="Detected (or requested) ISO 639-1 language code")
    language_probability: float | None
    duration_seconds: float
    model: str
    processing_ms: float


@router.post(
    "/transcribe",
    response_model=TranscriptionResponse,
    summary="Transcribe a voice recording to text",
    responses={
        413: {"description": "Recording larger than MAX_AUDIO_SIZE"},
        415: {"description": "The file contains no audio"},
        422: {"description": "Unreadable audio, longer than MAX_AUDIO_SECONDS, or no speech detected"},
        502: {"description": "Speech recognition failed"},
    },
)
async def transcribe(
    current_user: CurrentUser,
    _: VoiceLimit,
    file: Annotated[UploadFile, File(description="Audio recording: WebM/Ogg (Opus), MP4/M4A, MP3 or WAV")],
    language: Annotated[str | None, Form(min_length=2, max_length=5, pattern=r"^[a-zA-Z-]+$")] = None,
) -> TranscriptionResponse:
    """The transcript is returned for the user to review before it is sent as a chat message."""
    limit = get_settings().max_audio_size
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise FileTooLargeError(f"Recordings must be at most {limit // (1024 * 1024)} MB.")
    transcript, elapsed = await speech.transcribe_upload(
        data, file.filename or "recording", language.lower() if language else None
    )
    return TranscriptionResponse(
        text=transcript.text,
        language=transcript.language,
        language_probability=transcript.language_probability,
        duration_seconds=transcript.duration_seconds,
        model=transcript.model,
        processing_ms=elapsed,
    )


class SynthesisRequest(BaseModel):
    text: str = Field(
        min_length=1, max_length=50_000, description="Answer text; markdown and [n] markers are removed"
    )


@router.post(
    "/synthesize",
    response_class=Response,
    summary="Read text aloud (MP3)",
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "MP3 audio of the cleaned text"},
        422: {"description": "Nothing speakable in the text"},
        502: {"description": "Speech synthesis failed"},
    },
)
async def synthesize(current_user: CurrentUser, _: VoiceLimit, body: SynthesisRequest) -> Response:
    """Only the text sent is spoken. Long text is cut at a sentence boundary (X-Speech-Truncated: true)."""
    audio, cache_hit, elapsed = await tts.synthesize(body.text)
    return Response(
        content=audio.data,
        media_type=audio.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Speech-Voice": audio.voice,
            "X-Speech-Characters": str(audio.characters),
            "X-Speech-Truncated": "true" if audio.truncated else "false",
            "X-Speech-Cache": "hit" if cache_hit else "miss",
            "Server-Timing": f"tts;dur={elapsed}",
        },
    )
