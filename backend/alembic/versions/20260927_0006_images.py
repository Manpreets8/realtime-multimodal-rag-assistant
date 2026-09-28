"""Create images (uploads attached to chat messages).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('images',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.Uuid(), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('media_type', sa.String(length=50), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name=op.f('fk_images_message_id_messages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_images_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_images')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_images_storage_key'))
    )
    op.create_index(op.f('ix_images_message_id'), 'images', ['message_id'], unique=False)
    op.create_index(op.f('ix_images_user_id'), 'images', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_images_user_id'), table_name='images')
    op.drop_index(op.f('ix_images_message_id'), table_name='images')
    op.drop_table('images')
