"""add order message reference (bot/chat/message id)

Revision ID: 0006_order_message_ref
Revises: 0005_user_banned
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_order_message_ref"
down_revision: str | None = "0005_user_banned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("bot_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "message_id")
    op.drop_column("orders", "chat_id")
    op.drop_column("orders", "bot_id")
