"""SQLAlchemy 2.0 ORM models."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrderStatusEnum(enum.StrEnum):
    """Local lifecycle states. Mirrors WATA + a few bot-side states."""

    NEW = "New"  # created in DB, order not yet created at WATA
    PENDING = "Pending"  # awaiting payment
    REVIEW = "Review"  # paid, awaiting merchant confirmation
    PAID = "Paid"  # confirmed, being fulfilled
    SUCCESS = "Success"  # stars delivered
    REFUNDED = "Refunded"  # rejected / money returned
    FAIL = "Fail"  # fulfilment failed
    ERROR = "Error"  # bot-side failure creating the order


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    orders: Mapped[list[Order]] = relationship(back_populates="buyer", lazy="selectin")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    buyer_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False
    )
    target_username: Mapped[str] = mapped_column(String(64), nullable=False)

    count: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    commission: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    margin: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), default=OrderStatusEnum.NEW.value, nullable=False
    )
    payment_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_webhook: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    buyer: Mapped[User] = relationship(back_populates="orders", lazy="selectin")

    __table_args__ = (
        Index("ix_orders_order_id", "order_id"),
        Index("ix_orders_buyer_tg_id", "buyer_tg_id"),
    )
