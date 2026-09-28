"""Create conversations, messages and citations.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('conversations',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('knowledge_base_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name=op.f('fk_conversations_knowledge_base_id_knowledge_bases'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_conversations_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index(op.f('ix_conversations_knowledge_base_id'), 'conversations', ['knowledge_base_id'], unique=False)
    op.create_index('ix_conversations_user_id_updated_at', 'conversations', ['user_id', 'updated_at'], unique=False)
    op.create_table('messages',
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.Enum('user', 'assistant', name='message_role', native_enum=False, create_constraint=False, length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('answer_type', sa.String(length=20), nullable=True),
    sa.Column('knowledge_base_id', sa.Uuid(), nullable=True),
    sa.Column('retrieval_query', sa.Text(), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('truncated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('timings_ms', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('retrieval_stats', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f('ck_messages_message_role')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name=op.f('fk_messages_knowledge_base_id_knowledge_bases'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    op.create_index('ix_messages_conversation_id_created_at', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_table('citations',
    sa.Column('message_id', sa.Uuid(), nullable=False),
    sa.Column('source_number', sa.Integer(), nullable=False),
    sa.Column('cited', sa.Boolean(), nullable=False),
    sa.Column('chunk_id', sa.Uuid(), nullable=True),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('knowledge_base_id', sa.Uuid(), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('section', sa.String(length=300), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('rerank_score', sa.Double(), nullable=True),
    sa.Column('similarity', sa.Double(), nullable=True),
    sa.Column('quotes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('answer_spans', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['document_chunks.id'], name=op.f('fk_citations_chunk_id_document_chunks'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_citations_document_id_documents'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name=op.f('fk_citations_knowledge_base_id_knowledge_bases'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name=op.f('fk_citations_message_id_messages'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_citations'))
    )
    op.create_index(op.f('ix_citations_message_id'), 'citations', ['message_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_citations_message_id'), table_name='citations')
    op.drop_table('citations')
    op.drop_index('ix_messages_conversation_id_created_at', table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_conversations_user_id_updated_at', table_name='conversations')
    op.drop_index(op.f('ix_conversations_knowledge_base_id'), table_name='conversations')
    op.drop_table('conversations')
