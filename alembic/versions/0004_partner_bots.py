"""add partner bots and order partner attribution

Revision ID: 0004_partner_bots
Revises: 0003_referral_and_withdrawals
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_partner_bots"
down_revision: str | None = "0003_referral_and_withdrawals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("partner_owner_tg_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "partner_earning",
            sa.Numeric(precision=12, scale=2),
            server_default="0",
            nullable=False,
        ),
    )

    op.create_table(
        "partner_bots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column(
            "markup_percent",
            sa.Numeric(precision=5, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot_id"),
    )
    op.create_index("ix_partner_bots_owner", "partner_bots", ["owner_tg_id"])


def downgrade() -> None:
    op.drop_index("ix_partner_bots_owner", table_name="partner_bots")
    op.drop_table("partner_bots")
    op.drop_column("orders", "partner_earning")
    op.drop_column("orders", "partner_owner_tg_id")
