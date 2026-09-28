from app.llm.claude import LLMUnavailableError
from app.rag.conversation import (
    QUERY_REWRITE_SYSTEM_PROMPT,
    HistoryMessage,
    fallback_query,
    rewrite_query,
    trim_history,
)
from app.services.chat_service import make_title
from tests.fakes import ScriptedLLM

HISTORY = [
    HistoryMessage("user", "What is the leave policy?"),
    HistoryMessage("assistant", "Employees get 18 days of annual leave."),
    HistoryMessage("user", "And sick leave?"),
    HistoryMessage("assistant", "10 days of sick leave."),
]


def test_trim_history_keeps_the_latest_messages_starting_with_a_user_turn() -> None:
    trimmed = trim_history(HISTORY, max_messages=3, max_chars=1000)

    # The last 3 would start with an assistant message; the API needs a user message first.
    assert [m.role for m in trimmed] == ["user", "assistant"]
    assert trimmed[0].content == "And sick leave?"


def test_trim_history_caps_long_messages_and_can_be_disabled() -> None:
    long = [HistoryMessage("user", "x" * 5000)]

    assert trim_history(long, max_messages=10, max_chars=200)[0].content == "x" * 200 + " …"
    assert trim_history(HISTORY, max_messages=0, max_chars=200) == []


async def test_rewrite_sends_the_transcript_with_a_small_budget() -> None:
    llm = ScriptedLLM()
    llm.rewrite = "Query: sick leave carry over policy\n(extra line ignored)"

    query, info = await rewrite_query(llm, HISTORY, "Can it be carried over?", effort="low")

    assert query == "sick leave carry over policy"
    assert info["rewritten"] is True
    [call] = llm.calls
    assert call["system"] == QUERY_REWRITE_SYSTEM_PROMPT
    assert call["effort"] == "low" and call["max_tokens"] == 2000
    transcript = call["messages"][0]["content"]
    assert "User: What is the leave policy?" in transcript
    assert "Assistant: 10 days of sick leave." in transcript
    assert transcript.endswith("Latest message: Can it be carried over?")


async def test_no_history_means_no_rewrite_call() -> None:
    llm = ScriptedLLM()

    assert await rewrite_query(llm, [], "Standalone question?", effort="low") == (
        "Standalone question?",
        {"rewritten": False},
    )
    assert llm.calls == []


async def test_rewrite_failure_falls_back_to_the_previous_user_question() -> None:
    llm = ScriptedLLM()
    llm.rewrite_fail_with = LLMUnavailableError("down")

    query, info = await rewrite_query(llm, HISTORY, "Can it be carried over?", effort="low")

    assert query == "And sick leave? Can it be carried over?"
    assert info["fallback"] is True


def test_fallback_query_without_previous_user_message() -> None:
    assert fallback_query([], "hello") == "hello"


def test_make_title() -> None:
    assert make_title("  What is   the leave policy? ") == "What is the leave policy?"
    long = "How many days of paid annual leave do full-time employees receive in their first year?"
    assert make_title(long) == "How many days of paid annual leave do full-time employees…"
    assert len(make_title("x" * 100)) <= 61
