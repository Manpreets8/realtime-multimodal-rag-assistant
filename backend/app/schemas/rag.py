import uuid

from pydantic import BaseModel, Field, field_validator

from app.rag.pipeline import AnswerType
from app.rag.retrieval import MAX_QUERY_LENGTH
from app.schemas.retrieval import MAX_KNOWLEDGE_BASES_PER_SEARCH, SearchFilters


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    knowledge_base_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=MAX_KNOWLEDGE_BASES_PER_SEARCH,
        description="Empty for a general (non-RAG) answer",
    )
    filters: SearchFilters | None = None

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


class CitationCheckOut(BaseModel):
    cited_sources: int
    quotes: int
    verified_quotes: int = Field(description="Quotes found verbatim in their source")
    rejected: int = Field(description="Citations to a source that wasn't sent (dropped)")


class RetrievalStats(BaseModel):
    """What each pipeline stage did for this answer."""

    vector_candidates: int
    keyword_candidates: int
    filtered_out: int = Field(description="Dropped by the similarity threshold")
    duplicates_removed: int = 0
    filter_documents: int | None = Field(default=None, description="Documents matching the filters, if any")
    reranked: bool
    reranker: str | None = Field(
        default=None, description="Model that scored the passages (None: retrieval order)"
    )
    rerank_candidates: int | None = Field(default=None, description="Passages given to the reranker")
    below_rerank_threshold: int = 0
    over_budget: int = 0
    context_passages: int | None = None
    context_chars: int | None = None
    citation_check: CitationCheckOut | None = None


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
