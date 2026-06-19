"""add users.language

Revision ID: 0002_add_user_language
Revises: 0001_initial
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_user_language"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("language", sa.String(length=2), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "language")
