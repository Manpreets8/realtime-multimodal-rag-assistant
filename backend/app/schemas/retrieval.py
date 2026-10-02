import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.retrieval import MAX_QUERY_LENGTH, FusionMethod, RetrievalFilters, SearchMode
from app.utils.files import SUPPORTED_FILE_TYPES

MAX_KNOWLEDGE_BASES_PER_SEARCH = 10


class SearchFilters(BaseModel):
    """Restrict retrieval to some documents of the selected knowledge bases. Empty fields don't
    filter; IDs of documents that aren't yours simply match nothing."""

    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    file_types: list[str] = Field(default_factory=list, max_length=10, description='Extensions, e.g. ".pdf"')
    uploaded_after: datetime | None = None
    uploaded_before: datetime | None = None

    @field_validator("file_types")
    @classmethod
    def _known_types(cls, values: list[str]) -> list[str]:
        normalised = [value.lower() if value.startswith(".") else f".{value.lower()}" for value in values]
        unknown = sorted(set(normalised) - set(SUPPORTED_FILE_TYPES))
        if unknown:
            raise ValueError(f"Unsupported file types: {', '.join(unknown)}.")
        return normalised

    def to_filters(self) -> RetrievalFilters:
        return RetrievalFilters(
            document_ids=tuple(self.document_ids),
            extensions=tuple(self.file_types),
            uploaded_after=self.uploaded_after,
            uploaded_before=self.uploaded_before,
        )


class SearchOptions(BaseModel):
    """Per-search overrides of the server's retrieval settings, for exploring how hybrid search
    behaves (the Search tab's advanced settings). Answers and chat always use the server settings."""

    candidates: int | None = Field(
        default=None, ge=1, le=100, description="Per retriever, before fusion (TOP_K)"
    )
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    fusion: FusionMethod | None = None
    alpha: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Weighted fusion: weight of similarity"
    )
    topic: bool = Field(
        default=False,
        description="Treat the query as a topic: drop request phrasing such as 'Find everything related to'",
    )
    rerank: bool = Field(
        default=False,
        description="Re-score the top RERANK_CANDIDATES with the configured reranker, as answers do",
    )


class SearchParameters(BaseModel):
    """The parameters a search actually used (server settings plus any overrides)."""

    candidates: int
    limit: int
    similarity_threshold: float
    fusion: FusionMethod
    alpha: float
    dedup_threshold: float | None
    rerank: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    knowledge_base_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=MAX_KNOWLEDGE_BASES_PER_SEARCH,
        description="Empty (the default) searches all of your knowledge bases",
    )
    mode: SearchMode = SearchMode.HYBRID
    limit: int | None = Field(default=None, ge=1, le=50, description="Defaults to RERANK_TOP_K")
    filters: SearchFilters | None = None
    options: SearchOptions | None = None

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query cannot be empty.")
        return value

    @field_validator("knowledge_base_ids")
    @classmethod
    def _deduplicate(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    chunk_index: int
    page_number: int | None
    section: str | None
    content: str
    score: float
    similarity: float | None
    keyword_score: float | None
    vector_rank: int | None
    keyword_rank: int | None
    relevance: Literal["high", "medium", "low"] | None = Field(
        default=None,
        description="Plain-language band of the rerank score, for rerankers with measured bands only",
    )
    rerank_score: float | None = Field(
        default=None, description="Reranker relevance score; only when reranking was requested and succeeded"
    )


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    results: list[SearchHit]
    vector_candidates: int
    keyword_candidates: int
    filtered_out: int = Field(description="Candidates dropped by the similarity threshold")
    duplicates_removed: int = Field(default=0, description="Near-duplicate passages dropped")
    filter_documents: int | None = Field(default=None, description="Documents matching the filters, if any")
    similarity_threshold: float
    parameters: SearchParameters | None = None
    reranker: str | None = Field(default=None, description="Model that reranked the results, if any")
    timings_ms: dict[str, float]
