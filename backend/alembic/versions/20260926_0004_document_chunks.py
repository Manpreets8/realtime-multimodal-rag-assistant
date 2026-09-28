"""Create document_chunks with pgvector (HNSW) and full-text (GIN) indexes.

The vector size (384) matches BAAI/bge-small-en-v1.5; see EMBEDDING_COLUMN_DIMENSIONS.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('document_chunks',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('knowledge_base_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('section', sa.String(length=300), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('embedding', Vector(384), nullable=False),
    sa.Column('content_tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', content)", persisted=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_chunks_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name=op.f('fk_document_chunks_knowledge_base_id_knowledge_bases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_document_chunks_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_chunks')),
    sa.UniqueConstraint('document_id', 'chunk_index', name=op.f('uq_document_chunks_document_id_chunk_index'))
    )
    op.create_index('ix_document_chunks_content_tsv', 'document_chunks', ['content_tsv'], unique=False, postgresql_using='gin')
    op.create_index('ix_document_chunks_embedding_hnsw', 'document_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_document_chunks_knowledge_base_id'), 'document_chunks', ['knowledge_base_id'], unique=False)
    op.create_index(op.f('ix_document_chunks_user_id'), 'document_chunks', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_chunks_user_id'), table_name='document_chunks')
    op.drop_index(op.f('ix_document_chunks_knowledge_base_id'), table_name='document_chunks')
    op.drop_index('ix_document_chunks_embedding_hnsw', table_name='document_chunks', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_document_chunks_content_tsv', table_name='document_chunks', postgresql_using='gin')
    op.drop_table('document_chunks')
