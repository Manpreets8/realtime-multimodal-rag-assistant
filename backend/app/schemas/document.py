import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models import DocumentStatus
from app.services.ingestion_progress import IngestionProgress


class DocumentMetadata(BaseModel):
    """What extraction found. Title, author and date come from the file's own properties
    (or a Markdown document's first heading) and are absent when the file doesn't say."""

    title: str | None = None
    author: str | None = None
    document_date: date | None = None
    word_count: int
    character_count: int
    section_count: int  # headed sections (DOCX/Markdown); PDFs are split by page instead
    table_count: int = 0  # DOCX tables


class ProcessingStats(BaseModel):
    """Timings of the last successful run, per pipeline stage."""

    extraction_ms: int  # reading the file, cleaning the text, reading its metadata
    chunking_ms: int
    embedding_ms: int
    indexing_ms: int  # writing passages and vectors to PostgreSQL/pgvector
    total_ms: int
    embedding_model: str
    characters: int


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    extension: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    error_message: str | None
    error_code: str | None = None
    page_count: int | None
    chunk_count: int
    created_at: datetime
    processing_started_at: datetime | None
    processed_at: datetime | None
    extracted_metadata: DocumentMetadata | None = None
    processing_stats: ProcessingStats | None = None
    # Live detail while `processing` (from Redis); null otherwise or when unavailable.
    progress: IngestionProgress | None = None


class DocumentListItem(DocumentRead):
    knowledge_base_name: str


class DocumentPage(BaseModel):
    items: list[DocumentListItem]
    total: int


class SupportedFileType(BaseModel):
    extension: str
    content_type: str
    label: str


class UploadConfig(BaseModel):
    max_file_size: int
    supported_types: list[SupportedFileType]


class DocumentChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    page_number: int | None
    section: str | None
    content: str
    char_count: int


class ContextChunk(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    page_number: int | None
    section: str | None
    content: str


class ChunkContext(BaseModel):
    chunk: ContextChunk
    before: list[ContextChunk]
    after: list[ContextChunk]
    document: DocumentRead
