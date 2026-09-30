"""Add users.role (user | admin).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing accounts become regular users; admins are promoted explicitly (python -m app.cli).
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.Enum("user", "admin", name="user_role", native_enum=False, create_constraint=False, length=16),
            server_default="user",
            nullable=False,
        ),
    )
    op.create_check_constraint(op.f("ck_users_user_role"), "users", "role IN ('user', 'admin')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_user_role"), "users", type_="check")
    op.drop_column("users", "role")
