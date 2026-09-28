"""Create knowledge_bases and documents tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('knowledge_bases',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_knowledge_bases_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_bases'))
    )
    op.create_index(op.f('ix_knowledge_bases_user_id'), 'knowledge_bases', ['user_id'], unique=False)
    op.create_index('uq_knowledge_bases_user_id_lower_name', 'knowledge_bases', ['user_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('documents',
    sa.Column('knowledge_base_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('extension', sa.String(length=16), nullable=False),
    sa.Column('content_type', sa.String(length=127), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('status', sa.Enum('uploaded', 'processing', 'completed', 'failed', name='document_status', native_enum=False, create_constraint=False, length=20), server_default='uploaded', nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('uploaded', 'processing', 'completed', 'failed')", name=op.f('ck_documents_document_status')),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name=op.f('fk_documents_knowledge_base_id_knowledge_bases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_documents_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents')),
    sa.UniqueConstraint('knowledge_base_id', 'checksum_sha256', name=op.f('uq_documents_knowledge_base_id_checksum_sha256')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_documents_storage_key'))
    )
    op.create_index('ix_documents_knowledge_base_id_created_at', 'documents', ['knowledge_base_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)
    op.create_index(op.f('ix_documents_user_id'), 'documents', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_documents_user_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_status'), table_name='documents')
    op.drop_index('ix_documents_knowledge_base_id_created_at', table_name='documents')
    op.drop_table('documents')
    op.drop_index('uq_knowledge_bases_user_id_lower_name', table_name='knowledge_bases')
    op.drop_index(op.f('ix_knowledge_bases_user_id'), table_name='knowledge_bases')
    op.drop_table('knowledge_bases')
