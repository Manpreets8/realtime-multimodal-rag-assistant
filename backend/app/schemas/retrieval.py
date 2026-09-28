import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.retrieval import MAX_QUERY_LENGTH, SearchMode

MAX_KNOWLEDGE_BASES_PER_SEARCH = 10


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    knowledge_base_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_KNOWLEDGE_BASES_PER_SEARCH)
    mode: SearchMode = SearchMode.HYBRID
    limit: int | None = Field(default=None, ge=1, le=50, description="Defaults to RERANK_TOP_K")

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
    similarity_threshold: float
    timings_ms: dict[str, float]
