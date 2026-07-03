"""add crypto (TON/GreenGamePay) order fields

Revision ID: 0007_crypto_orders
Revises: 0006_order_message_ref
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_crypto_orders"
down_revision: str | None = "0006_order_message_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("provider", sa.String(length=8), nullable=False, server_default="wata"),
    )
    op.add_column("orders", sa.Column("ton_amount", sa.Numeric(18, 9), nullable=True))
    op.add_column("orders", sa.Column("ggp_tx_id", sa.String(length=128), nullable=True))
    # Drop the server default now existing rows are backfilled; the ORM supplies it.
    op.alter_column("orders", "provider", server_default=None)


def downgrade() -> None:
    op.drop_column("orders", "ggp_tx_id")
    op.drop_column("orders", "ton_amount")
    op.drop_column("orders", "provider")
