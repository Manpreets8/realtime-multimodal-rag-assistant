import uuid

from pydantic import BaseModel, Field, model_validator

from app.schemas.rag import CitationCheckOut, CitationOut, Source, Usage


class CompareRequest(BaseModel):
    document_a_id: uuid.UUID = Field(description="The first (original) document")
    document_b_id: uuid.UUID = Field(description="The second (revised) document")
    analysis: bool = Field(default=True, description="Also ask the AI model for a cited comparison")

    @model_validator(mode="after")
    def _different_documents(self) -> "CompareRequest":
        if self.document_a_id == self.document_b_id:
            raise ValueError("Choose two different documents.")
        return self


class ComparedDocument(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    page_count: int | None
    sentences: int = Field(description="Distinct sentences in the indexed text")
    coverage: float | None = Field(
        default=None, description="Share of the text given to the AI model (1.0 = all); null without analysis"
    )


class TextUnit(BaseModel):
    """A sentence (or list item) exactly as it appears in the document, with where it is."""

    text: str
    chunk_id: uuid.UUID
    page_number: int | None
    section: str | None


class ModifiedUnit(BaseModel):
    before: TextUnit = Field(description="In document A")
    after: TextUnit = Field(description="In document B")
    similarity: float = Field(description="Character similarity of the two sentences, 0..1")


class DifferenceCounts(BaseModel):
    added: int
    removed: int
    modified: int
    common: int


class TextDifferences(BaseModel):
    """Computed by comparing sentences, without AI: every item is real text from a document."""

    counts: DifferenceCounts
    overlap: float = Field(description="Share of the two documents' sentences that are identical (0..1)")
    added: list[TextUnit] = Field(description="Only in document B")
    removed: list[TextUnit] = Field(description="Only in document A")
    modified: list[ModifiedUnit]
    common: list[TextUnit]
    listed_limit: int = Field(description="Each list holds at most this many items; counts are complete")


class ComparisonAnalysis(BaseModel):
    text: str = Field(description="Markdown with the six comparison sections; citations map to sources")
    citations: list[CitationOut]
    sources: list[Source]
    cited_documents: list[uuid.UUID] = Field(description="Which of the two documents the analysis cites")
    citation_check: CitationCheckOut
    model: str
    usage: Usage
    truncated: bool


class CompareResponse(BaseModel):
    document_a: ComparedDocument
    document_b: ComparedDocument
    differences: TextDifferences
    analysis: ComparisonAnalysis | None
    analysis_unavailable: str | None = Field(
        default=None, description="Why there is no AI analysis (not requested, not configured, or failed)"
    )
    timings_ms: dict[str, float]
