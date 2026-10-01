import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.retrieval import MAX_QUERY_LENGTH, RetrievalFilters, SearchMode
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


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    knowledge_base_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_KNOWLEDGE_BASES_PER_SEARCH)
    mode: SearchMode = SearchMode.HYBRID
    limit: int | None = Field(default=None, ge=1, le=50, description="Defaults to RERANK_TOP_K")
    filters: SearchFilters | None = None

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
    timings_ms: dict[str, float]
