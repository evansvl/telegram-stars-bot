"""add referral program and withdrawals

Revision ID: 0003_referral_and_withdrawals
Revises: 0002_add_user_language
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_referral_and_withdrawals"
down_revision: str | None = "0002_add_user_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referred_by", sa.BigInteger(), nullable=True))

    op.create_table(
        "referral_earnings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("referrer_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index(
        "ix_referral_earnings_referrer", "referral_earnings", ["referrer_tg_id"]
    )

    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("destination", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("proof_type", sa.String(length=8), nullable=True),
        sa.Column("proof_value", sa.String(length=512), nullable=True),
        sa.Column("reject_reason", sa.String(length=512), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_withdrawals_user", "withdrawals", ["user_tg_id"])
    op.create_index("ix_withdrawals_status", "withdrawals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_withdrawals_status", table_name="withdrawals")
    op.drop_index("ix_withdrawals_user", table_name="withdrawals")
    op.drop_table("withdrawals")
    op.drop_index("ix_referral_earnings_referrer", table_name="referral_earnings")
    op.drop_table("referral_earnings")
    op.drop_column("users", "referred_by")
