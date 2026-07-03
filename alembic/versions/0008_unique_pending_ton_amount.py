"""unique TON amount among pending crypto orders

Guarantees no two still-unpaid (PENDING) GreenGamePay orders share a TON amount,
so a single deposit can never satisfy two orders. Partial unique index (Postgres).

Revision ID: 0008_unique_pending_ton_amount
Revises: 0007_crypto_orders
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_unique_pending_ton_amount"
down_revision: str | None = "0007_crypto_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_orders_pending_ton_amount"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "orders",
        ["ton_amount"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'ggp' AND status = 'Pending' AND ton_amount IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="orders")
