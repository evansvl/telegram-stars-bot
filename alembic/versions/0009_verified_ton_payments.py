"""store verified TON payments and enforce the active-order lifecycle

Revision ID: 0009_verified_ton_payments
Revises: 0008_unique_pending_ton_amount
Create Date: 2026-09-01
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_verified_ton_payments"
down_revision: str | None = "0008_unique_pending_ton_amount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TON_AMOUNT_INDEX = "uq_orders_pending_ton_amount"
_VERIFIED_TON_AMOUNT_INDEX = "uq_orders_verified_ton_amount"
_ACTIVE_BUYER_INDEX = "uq_orders_active_ggp_buyer"
_TX_HASH_CONSTRAINT = "uq_orders_ton_payment_tx_hash"
_ACTIVE_STATES = "'Pending', 'Review', 'Paid'"


def _legacy_payment_ttl_minutes() -> int:
    """Use the deployment's pre-migration invoice TTL for existing Pending rows."""
    try:
        value = int(os.getenv("CRYPTO_PAYMENT_TTL_MIN", "60"))
    except ValueError:
        return 60
    return value if 1 <= value <= 1440 else 60


def upgrade() -> None:
    op.add_column("orders", sa.Column("ton_payment_tx_hash", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("ton_payment_lt", sa.BigInteger(), nullable=True))
    op.add_column(
        "orders", sa.Column("ton_payment_utime", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("payment_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(_TX_HASH_CONSTRAINT, "orders", ["ton_payment_tx_hash"])

    # Preserve the deployment's configured invoice window for pre-migration rows
    # instead of extending it from migration time.
    legacy_ttl = _legacy_payment_ttl_minutes()
    op.execute(
        f"""
        UPDATE orders
        SET payment_expires_at = created_at + INTERVAL '{legacy_ttl} minutes'
        WHERE provider = 'ggp'
          AND status = 'Pending'
          AND payment_expires_at IS NULL
        """
    )

    op.drop_index(_OLD_TON_AMOUNT_INDEX, table_name="orders")

    # The former index covered Pending only. If a process stopped with a Paid claim,
    # it may share its amount or buyer with a newer Pending row. Retain the verified /
    # in-flight row and expire only the still-unpaid duplicate; never discard payment
    # evidence automatically. Any impossible duplicate Review/Paid rows intentionally
    # make index creation fail for manual reconciliation.
    op.execute(
        f"""
        UPDATE orders
        SET status = 'Expired'
        WHERE status = 'Pending'
          AND id IN (
              SELECT id
              FROM (
                  SELECT
                      id,
                      ROW_NUMBER() OVER (
                          PARTITION BY ton_amount
                          ORDER BY
                              CASE status
                                  WHEN 'Paid' THEN 0
                                  WHEN 'Review' THEN 1
                                  ELSE 2
                              END,
                              created_at ASC,
                              id ASC
                      ) AS rn
                  FROM orders
                  WHERE provider = 'ggp'
                    AND status IN ({_ACTIVE_STATES})
                    AND ton_amount IS NOT NULL
              ) duplicate_amounts
              WHERE duplicate_amounts.rn > 1
          )
        """
    )
    op.execute(
        f"""
        UPDATE orders
        SET status = 'Expired'
        WHERE status = 'Pending'
          AND id IN (
              SELECT id
              FROM (
                  SELECT
                      id,
                      ROW_NUMBER() OVER (
                          PARTITION BY buyer_tg_id
                          ORDER BY
                              CASE status
                                  WHEN 'Paid' THEN 0
                                  WHEN 'Review' THEN 1
                                  ELSE 2
                              END,
                              created_at ASC,
                              id ASC
                      ) AS rn
                  FROM orders
                  WHERE provider = 'ggp'
                    AND status IN ({_ACTIVE_STATES})
                    AND buyer_tg_id <> 0
              ) duplicate_buyers
              WHERE duplicate_buyers.rn > 1
          )
        """
    )

    active_predicate = sa.text(
        "provider = 'ggp' AND status IN ('Pending', 'Review', 'Paid') " "AND buyer_tg_id <> 0"
    )
    # payment_expires_at marks invoices created/backfilled for the verified flow.
    # Keep their amounts unique after terminal states too, so a late old transfer
    # can never satisfy a future order. Legacy terminal rows remain unchanged; the
    # allocator also reads their amounts and will not issue them again.
    op.create_index(
        _VERIFIED_TON_AMOUNT_INDEX,
        "orders",
        ["ton_amount"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'ggp' AND payment_expires_at IS NOT NULL " "AND ton_amount IS NOT NULL"
        ),
    )
    op.create_index(
        _ACTIVE_BUYER_INDEX,
        "orders",
        ["buyer_tg_id"],
        unique=True,
        postgresql_where=active_predicate,
    )


def downgrade() -> None:
    op.drop_index(_ACTIVE_BUYER_INDEX, table_name="orders")
    op.drop_index(_VERIFIED_TON_AMOUNT_INDEX, table_name="orders")
    op.create_index(
        _OLD_TON_AMOUNT_INDEX,
        "orders",
        ["ton_amount"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'ggp' AND status = 'Pending' AND ton_amount IS NOT NULL"
        ),
    )
    op.drop_constraint(_TX_HASH_CONSTRAINT, "orders", type_="unique")
    op.drop_column("orders", "payment_expires_at")
    op.drop_column("orders", "ton_payment_utime")
    op.drop_column("orders", "ton_payment_lt")
    op.drop_column("orders", "ton_payment_tx_hash")
