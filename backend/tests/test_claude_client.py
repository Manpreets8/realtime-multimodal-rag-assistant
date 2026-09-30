"""ClaudeClient against the real Anthropic SDK with a mocked HTTP transport.

The transport returns genuine server-sent-event streams, so these tests exercise
our request construction and the SDK's stream parsing (including citation
deltas), not just our own code."""

import json

import anthropic
import httpx2
import pytest

from app.llm.base import (
    ImagePart,
    LLMError,
    LLMNotConfiguredError,
    LLMRefusalError,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    SourcePart,
)
from app.llm.claude import REFUSAL_FALLBACK_BETA, ClaudeClient, to_anthropic_messages


def sse(*events: dict) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


def message_stream(blocks: list[tuple[str, list[dict]]], stop_reason: str = "end_turn") -> bytes:
    """Build a stream for text blocks given as (text, citations)."""
    events: list[dict] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 812, "output_tokens": 1},
            },
        }
    ]
    for index, (text, citations) in enumerate(blocks):
        events.append(
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": "", "citations": None},
            }
        )
        for citation in citations:
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "citations_delta", "citation": citation},
                }
            )
        events.append(
            {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}
        )
        events.append({"type": "content_block_stop", "index": index})
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 57},
        }
    )
    events.append({"type": "message_stop"})
    return sse(*events)


def char_citation(document_index: int, text: str) -> dict:
    return {
        "type": "char_location",
        "cited_text": text,
        "document_index": document_index,
        "document_title": f"doc {document_index}",
        "start_char_index": 0,
        "end_char_index": len(text),
    }


def make_client(handler, **options) -> tuple[ClaudeClient, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def recording(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    sdk = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recording)),
    )
    return ClaudeClient(sdk, model="claude-opus-5", max_tokens=4000, **options), requests


def streaming(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


MESSAGES = [Message.user(SourcePart("18 days of leave.", "handbook.pdf (page 2)"), "How much leave?")]

# MESSAGES as the Messages API receives them.
WIRE_MESSAGES = [
    {
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": "18 days of leave."},
                "title": "handbook.pdf (page 2)",
                "citations": {"enabled": True},
            },
            {"type": "text", "text": "How much leave?"},
        ],
    }
]


async def test_request_uses_streaming_beta_endpoint_with_refusal_fallback() -> None:
    client, requests = make_client(lambda r: streaming(message_stream([("Answer.", [])])), effort="medium")

    await client.generate(system="SYSTEM", messages=MESSAGES)

    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert REFUSAL_FALLBACK_BETA in request.headers["anthropic-beta"]
    assert request.headers["x-api-key"] == "sk-ant-test"
    assert body["model"] == "claude-opus-5"
    assert body["stream"] is True
    assert body["fallbacks"] == "default"
    assert body["max_tokens"] == 4000
    assert body["system"] == "SYSTEM"
    assert body["output_config"] == {"effort": "medium"}
    assert body["messages"] == WIRE_MESSAGES
    assert "thinking" not in body and "temperature" not in body


def test_neutral_messages_translate_to_the_messages_api_format() -> None:
    history = [
        Message.user(ImagePart("image/png", b"png-bytes"), "What is this?"),
        Message.assistant("A chart."),
    ]
    question = Message.user(SourcePart("untitled passage"), "And the trend?")

    assert to_anthropic_messages([*history, question]) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "cG5nLWJ5dGVz"},
                },
                {"type": "text", "text": "What is this?"},
            ],
        },
        {"role": "assistant", "content": "A chart."},  # a text-only turn is a plain string
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "text", "media_type": "text/plain", "data": "untitled passage"},
                    "citations": {"enabled": True},
                },
                {"type": "text", "text": "And the trend?"},
            ],
        },
    ]


async def test_optional_parameters_are_omitted_when_disabled() -> None:
    client, requests = make_client(lambda r: streaming(message_stream([("x", [])])), refusal_fallback=False)

    await client.generate(system="S", messages=MESSAGES)

    body = json.loads(requests[0].content)
    assert "fallbacks" not in body and "output_config" not in body
    assert REFUSAL_FALLBACK_BETA not in requests[0].headers.get("anthropic-beta", "")


async def test_text_blocks_and_citations_are_flattened_with_answer_offsets() -> None:
    body = message_stream(
        [
            ("Full-time employees get ", []),
            ("18 days of paid annual leave", [char_citation(0, "18 days of paid annual leave per year.")]),
            (" and ", []),
            (
                "10 sick days",
                [char_citation(1, "10 days of paid sick leave"), char_citation(0, "sick leave is separate")],
            ),
            (".", []),
        ]
    )
    client, _ = make_client(lambda r: streaming(body))

    response = await client.generate(system="S", messages=MESSAGES)

    assert response.text == "Full-time employees get 18 days of paid annual leave and 10 sick days."
    assert [(c.document_index, response.text[c.answer_start : c.answer_end]) for c in response.citations] == [
        (0, "18 days of paid annual leave"),
        (1, "10 sick days"),
        (0, "10 sick days"),
    ]
    assert response.citations[0].cited_text == "18 days of paid annual leave per year."
    assert (response.citations[0].source_start, response.citations[0].source_end) == (0, 38)
    assert (response.input_tokens, response.output_tokens) == (812, 57)
    assert response.model == "claude-opus-5"
    assert response.stop_reason == "end_turn" and not response.truncated


async def test_max_tokens_marks_the_answer_truncated() -> None:
    client, _ = make_client(
        lambda r: streaming(message_stream([("Partial answ", [])], stop_reason="max_tokens"))
    )

    response = await client.generate(system="S", messages=MESSAGES)

    assert response.truncated


async def test_refusal_is_raised_not_returned_as_an_answer() -> None:
    client, _ = make_client(lambda r: streaming(message_stream([], stop_reason="refusal")))

    with pytest.raises(LLMRefusalError):
        await client.generate(system="S", messages=MESSAGES)


def error(status: int, error_type: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": error_type, "message": "details"}}
    )


@pytest.mark.parametrize(
    ("response", "expected", "status"),
    [
        (error(401, "authentication_error"), LLMNotConfiguredError, 503),
        (error(429, "rate_limit_error"), LLMUnavailableError, 503),
        (error(529, "overloaded_error"), LLMUnavailableError, 503),
        (error(500, "api_error"), LLMUnavailableError, 503),
        (error(400, "invalid_request_error"), LLMError, 502),
    ],
)
async def test_api_errors_map_to_user_facing_errors(
    response: httpx2.Response, expected: type[LLMError], status: int
) -> None:
    client, _ = make_client(lambda r: response)

    with pytest.raises(expected) as raised:
        await client.generate(system="S", messages=MESSAGES)

    assert raised.value.status_code == status
    assert "details" not in raised.value.message  # provider error text is logged, not shown


async def test_timeouts_and_connection_errors() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(LLMTimeoutError):
        await make_client(timeout)[0].generate(system="S", messages=MESSAGES)
    with pytest.raises(LLMUnavailableError):
        await make_client(refused)[0].generate(system="S", messages=MESSAGES)


# --- streaming ------------------------------------------------------------------------------


class RecordingStream:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def text(self, delta: str) -> None:
        self.events.append(("text", delta))

    async def restart(self) -> None:
        self.events.append(("restart", ""))


def with_fallback(declined: str, answer: str, citation: dict) -> bytes:
    """A stream where the first model declines mid-answer and the fallback model answers."""
    start = {
        "type": "message_start",
        "message": {
            "id": "msg_fb",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 900, "output_tokens": 1},
        },
    }
    text_block = {"type": "text", "text": "", "citations": None}
    fallback_block = {
        "type": "fallback",
        "from": {"model": "claude-opus-5"},
        "to": {"model": "claude-sonnet-5"},
        "trigger": {"type": "refusal", "category": None},
    }
    return sse(
        start,
        {"type": "content_block_start", "index": 0, "content_block": text_block},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": declined}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": fallback_block},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": text_block},
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "citations_delta", "citation": citation},
        },
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": answer}},
        {"type": "content_block_stop", "index": 2},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 40},
        },
        {"type": "message_stop"},
    )


async def test_text_deltas_are_forwarded_as_they_arrive() -> None:
    body = message_stream([("Employees get ", []), ("18 days.", [char_citation(0, "18 days of leave.")])])
    client, _ = make_client(lambda request: streaming(body))
    stream = RecordingStream()

    response = await client.generate(system="s", messages=MESSAGES, stream=stream)

    assert stream.events == [("text", "Employees get "), ("text", "18 days.")]
    assert response.text == "Employees get 18 days."
    assert response.citations[0].answer_start == len("Employees get ")


async def test_a_mid_stream_fallback_restarts_the_stream_and_keeps_only_the_fallback_answer() -> None:
    body = with_fallback("I started to", "You get 18 days.", char_citation(0, "18 days of leave."))
    stream = RecordingStream()
    client, _ = make_client(lambda request: streaming(body))

    streamed = await client.generate(system="s", messages=MESSAGES, stream=stream)
    plain = await make_client(lambda request: streaming(body))[0].generate(system="s", messages=MESSAGES)

    assert stream.events == [("text", "I started to"), ("restart", ""), ("text", "You get 18 days.")]
    for response in (streamed, plain):
        assert response.text == "You get 18 days."  # the declined model's partial text is not the answer
        assert response.model == "claude-sonnet-5"
        assert [(c.answer_start, c.answer_end) for c in response.citations] == [(0, len("You get 18 days."))]


async def test_leading_whitespace_is_stripped_without_shifting_citations() -> None:
    body = message_stream([("\n\n", []), ("18 days.", [char_citation(0, "18 days of leave.")])])
    client, _ = make_client(lambda request: streaming(body))

    response = await client.generate(system="s", messages=MESSAGES)

    span = response.citations[0]
    assert response.text[span.answer_start : span.answer_end] == "18 days."
