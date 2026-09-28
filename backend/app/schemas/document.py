import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import DocumentStatus
from app.services.ingestion_progress import IngestionProgress


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
    page_count: int | None
    chunk_count: int
    created_at: datetime
    processing_started_at: datetime | None
    processed_at: datetime | None
    # Live detail while `processing` (from Redis); null otherwise or when unavailable.
    progress: IngestionProgress | None = None


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
