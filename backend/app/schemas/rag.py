import uuid

from pydantic import BaseModel, Field, field_validator

from app.rag.pipeline import AnswerType
from app.rag.retrieval import MAX_QUERY_LENGTH
from app.schemas.retrieval import MAX_KNOWLEDGE_BASES_PER_SEARCH


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    knowledge_base_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=MAX_KNOWLEDGE_BASES_PER_SEARCH,
        description="Empty for a general (non-RAG) answer",
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be empty.")
        return value

    @field_validator("knowledge_base_ids")
    @classmethod
    def _deduplicate(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class Source(BaseModel):
    number: int = Field(description="1-based; citations refer to sources by this number")
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    page_number: int | None
    section: str | None
    content: str
    rerank_score: float | None
    similarity: float | None


class QuoteOut(BaseModel):
    text: str
    start: int | None = Field(description="Offset of the quote in the source's content; null if not located")
    end: int | None


class CitationOut(BaseModel):
    source_number: int
    document_id: uuid.UUID
    filename: str
    page_number: int | None
    section: str | None
    quotes: list[QuoteOut]
    answer_spans: list[tuple[int, int]] = Field(
        description="Character ranges of the answer this source supports"
    )


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class RetrievalStats(BaseModel):
    vector_candidates: int
    keyword_candidates: int
    filtered_out: int
    reranked: bool


class AnswerResponse(BaseModel):
    question: str
    answer: str
    answer_type: AnswerType
    grounded: bool = Field(description="True when the answer cites at least one source")
    citations: list[CitationOut]
    sources: list[Source]
    model: str | None
    usage: Usage | None
    truncated: bool
    retrieval: RetrievalStats | None
    timings_ms: dict[str, float]
