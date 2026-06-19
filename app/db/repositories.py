"""Data-access repositories for users and orders."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatusEnum, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, tg_id: int, username: str | None) -> None:
        """Create the user or refresh their username (idempotent).

        Does not touch ``language`` so a user's chosen language is preserved.
        """
        stmt = (
            pg_insert(User)
            .values(tg_id=tg_id, username=username)
            .on_conflict_do_update(index_elements=[User.tg_id], set_={"username": username})
        )
        await self._session.execute(stmt)

    async def get_language(self, tg_id: int) -> str | None:
        return await self._session.scalar(select(User.language).where(User.tg_id == tg_id))

    async def set_language(self, tg_id: int, language: str) -> None:
        """Persist the user's language, creating the user row if needed."""
        stmt = (
            pg_insert(User)
            .values(tg_id=tg_id, language=language)
            .on_conflict_do_update(index_elements=[User.tg_id], set_={"language": language})
        )
        await self._session.execute(stmt)


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        order_id: str,
        buyer_tg_id: int,
        target_username: str,
        count: int,
        amount: Decimal,
        status: str = OrderStatusEnum.NEW.value,
    ) -> Order:
        order = Order(
            order_id=order_id,
            buyer_tg_id=buyer_tg_id,
            target_username=target_username,
            count=count,
            amount=amount,
            status=status,
        )
        self._session.add(order)
        await self._session.flush()
        return order

    async def get_by_order_id(self, order_id: str) -> Order | None:
        result = await self._session.execute(select(Order).where(Order.order_id == order_id))
        return result.scalar_one_or_none()

    async def list_for_buyer(self, buyer_tg_id: int, limit: int = 20) -> list[Order]:
        result = await self._session.execute(
            select(Order)
            .where(Order.buyer_tg_id == buyer_tg_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        order_id: str,
        *,
        status: str,
        price: Decimal | None = None,
        commission: Decimal | None = None,
        margin: Decimal | None = None,
        payment_link: str | None = None,
        raw_webhook: dict[str, Any] | None = None,
    ) -> Order | None:
        order = await self.get_by_order_id(order_id)
        if order is None:
            return None
        order.status = status
        if price is not None:
            order.price = price
        if commission is not None:
            order.commission = commission
        if margin is not None:
            order.margin = margin
        if payment_link is not None:
            order.payment_link = payment_link
        if raw_webhook is not None:
            order.raw_webhook = raw_webhook
        await self._session.flush()
        return order

    async def stats(self) -> dict[str, Any]:
        """Aggregate turnover / order count / total margin for paid+ orders."""
        paid_states = [
            OrderStatusEnum.PAID.value,
            OrderStatusEnum.SUCCESS.value,
        ]
        total_orders = await self._session.scalar(select(func.count()).select_from(Order))
        paid_filter = Order.status.in_(paid_states)
        turnover = await self._session.scalar(
            select(func.coalesce(func.sum(Order.amount), 0)).where(paid_filter)
        )
        margin = await self._session.scalar(
            select(func.coalesce(func.sum(Order.margin), 0)).where(paid_filter)
        )
        paid_count = await self._session.scalar(
            select(func.count()).select_from(Order).where(paid_filter)
        )
        return {
            "total_orders": int(total_orders or 0),
            "paid_orders": int(paid_count or 0),
            "turnover": Decimal(turnover or 0),
            "margin": Decimal(margin or 0),
        }
