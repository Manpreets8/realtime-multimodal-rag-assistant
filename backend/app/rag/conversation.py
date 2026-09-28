"""Conversation context for follow-up questions (text and images).

Two separate uses of history:

1. Retrieval. A follow-up such as "what about the second one?" has no searchable
   content of its own, and a question about a screenshot ("what does this error
   mean?") hides its key terms in the image. When there is history or an attached
   image, the LLM writes a standalone search query. If that call fails, a heuristic
   fallback prefixes the previous user question so the search still has context.
2. Answering. Recent turns, including images from earlier user messages, are sent
   before the grounded question, so the answer can refer back to them. Only the
   latest turn carries documents; earlier answers are text only.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from app.llm.claude import LLMClient, LLMError

logger = logging.getLogger(__name__)

MAX_REWRITTEN_QUERY_CHARS = 500
_REWRITE_MAX_TOKENS = 2000
_IMAGE_PLACEHOLDER = "(image)"

QUERY_REWRITE_SYSTEM_PROMPT = """\
You turn the latest message of a conversation into a standalone search query for a document \
search engine.

- Resolve references ("it", "that policy", "the second one") using the conversation.
- Keep the user's key terms, names, codes and numbers; add terms from earlier turns only if \
the latest message depends on them.
- If images are attached, include the key visible text the question is about (error messages \
and codes, titles, product or component names), so documents about it can be found.
- If the latest message is already standalone, return it unchanged.
- Output only the query: one line, no quotes, no explanation, no answer to the question."""


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    role: str  # "user" | "assistant"
    content: str
    # Image content blocks attached to a user message, kept for follow-ups about the image.
    images: tuple[dict[str, Any], ...] = ()


def trim_history(history: list[HistoryMessage], max_messages: int, max_chars: int) -> list[HistoryMessage]:
    """The most recent `max_messages` messages, each capped at `max_chars`, starting with a user turn
    (the Messages API requires the first message to be from the user)."""
    recent = history[-max_messages:] if max_messages else []
    while recent and recent[0].role != "user":
        recent = recent[1:]
    return [
        HistoryMessage(
            m.role, m.content if len(m.content) <= max_chars else m.content[:max_chars] + " …", m.images
        )
        for m in recent
    ]


def history_as_messages(history: list[HistoryMessage]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in history:
        if message.images:
            text = message.content or _IMAGE_PLACEHOLDER
            messages.append(
                {"role": message.role, "content": [*message.images, {"type": "text", "text": text}]}
            )
        else:
            messages.append({"role": message.role, "content": message.content})
    return messages


def _transcript(history: list[HistoryMessage], question: str) -> str:
    lines = [
        f"{'User' if m.role == 'user' else 'Assistant'}: {m.content or _IMAGE_PLACEHOLDER}" for m in history
    ]
    prefix = "Conversation so far:\n" + "\n".join(lines) + "\n\n" if lines else ""
    return f"{prefix}Latest message: {question}"


def _clean_query(text: str) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^(?:search query|query)\s*:\s*", "", line, flags=re.IGNORECASE)
    return line.strip().strip("\"'").strip()[:MAX_REWRITTEN_QUERY_CHARS]


def fallback_query(history: list[HistoryMessage], question: str) -> str:
    """Without the LLM: search with the previous user question as extra context."""
    previous = next((m.content for m in reversed(history) if m.role == "user" and m.content), "")
    return f"{previous} {question}".strip()[: MAX_REWRITTEN_QUERY_CHARS * 2] if previous else question


async def rewrite_query(
    llm: LLMClient,
    history: list[HistoryMessage],
    question: str,
    *,
    effort: str,
    images: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (search query, diagnostics). Never raises: falls back on LLM failure.

    Runs when there is history (to resolve references) or there are current images (to put
    their visible text into the query). Earlier turns' images are not re-sent for this."""
    if not history and not images:
        return question, {"rewritten": False}
    transcript = _transcript(history, question)
    content: str | list[dict[str, Any]] = (
        [*images, {"type": "text", "text": transcript}] if images else transcript
    )
    started = time.perf_counter()
    try:
        response = await llm.generate(
            system=QUERY_REWRITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            max_tokens=_REWRITE_MAX_TOKENS,
            effort=effort,
        )
        query = _clean_query(response.text)
    except LLMError as exc:
        logger.warning("query_rewrite_failed_using_fallback", extra={"error_code": exc.code})
        query = ""
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    if not query:
        return fallback_query(history, question), {
            "rewritten": False,
            "fallback": True,
            "rewrite_ms": elapsed,
        }
    return query, {"rewritten": query != question, "rewrite_ms": elapsed}
