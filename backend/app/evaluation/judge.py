"""LLM-as-judge for the answer metrics that string matching can't measure.

- Faithfulness: the answer is split into atomic claims and each claim is checked against
  the sources the answering model was given. Score = supported claims / claims.
- Answer relevance: does the answer address the question (1-5, reported as 0..1)?

The judge is forced to call a tool, so its verdict arrives as JSON matching a schema
instead of free text that has to be parsed. It sees exactly the passages the answering
model saw, so "supported" means supported by the retrieved context, not by the judge's
own knowledge.

Judges have biases (leniency, preferring longer answers), so treat these scores as a
signal to compare configurations, alongside the deterministic fact and citation checks,
not as ground truth.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

TOOL_NAME = "record_grading"

JUDGE_SYSTEM_PROMPT = """You grade answers produced by a retrieval-augmented assistant.

You receive the user's question, the numbered source passages the assistant was given, and the
assistant's answer.

1. Split the answer into atomic factual claims: one checkable statement each. Skip phrases that
   carry no factual content, such as "according to the handbook" or offers of further help.
2. For every claim, decide whether the source passages support it. A claim is supported only if
   the passages state it or it follows directly from them. Use only the passages, never your own
   knowledge: a claim that is true in the real world but absent from the passages is unsupported.
3. Rate how well the answer addresses the question:
   5 = fully answers it, directly and without padding
   4 = answers it with minor omissions or some irrelevant content
   3 = partly answers it
   2 = mostly misses the point
   1 = does not answer it
   An answer that correctly says the information is not in the sources, when the passages indeed
   don't contain it, rates 5.

Record the grading with the record_grading tool."""

GRADING_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Record the grading of the assistant's answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "supported": {"type": "boolean"},
                        "source_numbers": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Passages that support the claim (empty if unsupported)",
                        },
                    },
                    "required": ["claim", "supported", "source_numbers"],
                },
            },
            "answer_relevance": {"type": "integer", "minimum": 1, "maximum": 5},
            "relevance_reason": {"type": "string"},
        },
        "required": ["claims", "answer_relevance", "relevance_reason"],
    },
}


class JudgeError(Exception):
    """The judge could not grade this answer (API error or malformed verdict)."""


@dataclass(slots=True)
class ClaimVerdict:
    claim: str
    supported: bool
    source_numbers: list[int]


@dataclass(slots=True)
class JudgeVerdict:
    claims: list[ClaimVerdict]
    answer_relevance: int  # 1-5
    relevance_reason: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def faithfulness(self) -> float | None:
        if not self.claims:
            return None
        return sum(c.supported for c in self.claims) / len(self.claims)

    @property
    def answer_relevance_score(self) -> float:
        return (self.answer_relevance - 1) / 4


def build_judge_prompt(question: str, sources: Sequence[str], answer: str) -> str:
    passages = "\n\n".join(f'<passage number="{n}">\n{text}\n</passage>' for n, text in enumerate(sources, 1))
    return (
        f"<question>\n{question}\n</question>\n\n"
        f"<sources>\n{passages or '(no passages were retrieved)'}\n</sources>\n\n"
        f"<answer>\n{answer}\n</answer>"
    )


def parse_verdict(message: Any) -> JudgeVerdict:
    block = next((b for b in message.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
    if block is None:
        raise JudgeError(f"the judge did not call {TOOL_NAME} (stop_reason={message.stop_reason})")
    data = block.input
    try:
        relevance = int(data["answer_relevance"])
        claims = [
            ClaimVerdict(str(c["claim"]), bool(c["supported"]), [int(n) for n in c.get("source_numbers", [])])
            for c in data["claims"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise JudgeError(f"malformed verdict: {exc}") from exc
    if not 1 <= relevance <= 5:
        raise JudgeError(f"answer_relevance out of range: {relevance}")
    return JudgeVerdict(
        claims=claims,
        answer_relevance=relevance,
        relevance_reason=str(data.get("relevance_reason", "")),
        model=message.model,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )


class ClaudeJudge:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str, max_tokens: int = 4000) -> None:
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def grade(self, question: str, sources: Sequence[str], answer: str) -> JudgeVerdict:
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_judge_prompt(question, sources, answer)}],
                tools=[GRADING_TOOL],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except anthropic.APIError as exc:
            raise JudgeError(f"judge request failed: {type(exc).__name__}") from exc
        return parse_verdict(message)
