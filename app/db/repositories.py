"""Data-access repositories for users and orders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Order,
    OrderStatusEnum,
    PartnerBot,
    ReferralEarning,
    User,
    Withdrawal,
    WithdrawalStatus,
)


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

    async def get_referrer(self, tg_id: int) -> int | None:
        return await self._session.scalar(select(User.referred_by).where(User.tg_id == tg_id))

    async def set_referrer(self, tg_id: int, referrer_tg_id: int) -> bool:
        """Attribute ``tg_id`` to ``referrer_tg_id`` once; no-op if already set.

        Returns True if the attribution was applied. The user row is created if
        needed (e.g. brand-new user opening a referral link).
        """
        await self._session.execute(
            pg_insert(User)
            .values(tg_id=tg_id, referred_by=referrer_tg_id)
            .on_conflict_do_nothing(index_elements=[User.tg_id])
        )
        result = await self._session.execute(
            User.__table__.update()
            .where(User.tg_id == tg_id, User.referred_by.is_(None))
            .values(referred_by=referrer_tg_id)
        )
        return bool(result.rowcount)

    async def count_referrals(self, referrer_tg_id: int) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(User).where(User.referred_by == referrer_tg_id)
        )
        return int(count or 0)

    async def get_created_at(self, tg_id: int) -> datetime | None:
        return await self._session.scalar(select(User.created_at).where(User.tg_id == tg_id))

    async def get(self, tg_id: int) -> User | None:
        return await self._session.get(User, tg_id)

    async def is_banned(self, tg_id: int) -> bool:
        return bool(await self._session.scalar(select(User.banned).where(User.tg_id == tg_id)))

    async def set_banned(self, tg_id: int, banned: bool) -> None:
        stmt = (
            pg_insert(User)
            .values(tg_id=tg_id, banned=banned)
            .on_conflict_do_update(index_elements=[User.tg_id], set_={"banned": banned})
        )
        await self._session.execute(stmt)


class ReferralRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def credit(
        self, *, referrer_tg_id: int, referred_tg_id: int, order_id: str, amount: Decimal
    ) -> bool:
        """Record a referral earning, once per order. Returns True if inserted."""
        result = await self._session.execute(
            pg_insert(ReferralEarning)
            .values(
                referrer_tg_id=referrer_tg_id,
                referred_tg_id=referred_tg_id,
                order_id=order_id,
                amount=amount,
            )
            .on_conflict_do_nothing(index_elements=[ReferralEarning.order_id])
        )
        return bool(result.rowcount)

    async def total_earned(self, referrer_tg_id: int) -> Decimal:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ReferralEarning.amount), 0)).where(
                ReferralEarning.referrer_tg_id == referrer_tg_id
            )
        )
        return Decimal(total or 0)


class WithdrawalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, user_tg_id: int, amount: Decimal, method: str, destination: str
    ) -> Withdrawal:
        withdrawal = Withdrawal(
            user_tg_id=user_tg_id,
            amount=amount,
            method=method,
            destination=destination,
            status=WithdrawalStatus.PENDING.value,
        )
        self._session.add(withdrawal)
        await self._session.flush()
        return withdrawal

    async def get(self, withdrawal_id: int) -> Withdrawal | None:
        return await self._session.get(Withdrawal, withdrawal_id)

    async def list_for_user(self, user_tg_id: int, limit: int = 20) -> list[Withdrawal]:
        result = await self._session.execute(
            select(Withdrawal)
            .where(Withdrawal.user_tg_id == user_tg_id)
            .order_by(Withdrawal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def committed_total(self, user_tg_id: int) -> Decimal:
        """Sum of amounts that reduce a balance: pending (locked) + approved (paid)."""
        total = await self._session.scalar(
            select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
                Withdrawal.user_tg_id == user_tg_id,
                Withdrawal.status.in_(
                    [WithdrawalStatus.PENDING.value, WithdrawalStatus.APPROVED.value]
                ),
            )
        )
        return Decimal(total or 0)

    async def has_pending(self, user_tg_id: int) -> bool:
        found = await self._session.scalar(
            select(Withdrawal.id).where(
                Withdrawal.user_tg_id == user_tg_id,
                Withdrawal.status == WithdrawalStatus.PENDING.value,
            )
        )
        return found is not None

    async def list_pending(self, limit: int = 50) -> list[Withdrawal]:
        result = await self._session.execute(
            select(Withdrawal)
            .where(Withdrawal.status == WithdrawalStatus.PENDING.value)
            .order_by(Withdrawal.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def resolve(
        self,
        withdrawal_id: int,
        *,
        status: str,
        resolved_by: int,
        proof_type: str | None = None,
        proof_value: str | None = None,
        reject_reason: str | None = None,
    ) -> Withdrawal | None:
        """Move a PENDING withdrawal to APPROVED/REJECTED. No-op if not pending."""
        withdrawal = await self.get(withdrawal_id)
        if withdrawal is None or withdrawal.status != WithdrawalStatus.PENDING.value:
            return None
        withdrawal.status = status
        withdrawal.resolved_by = resolved_by
        withdrawal.proof_type = proof_type
        withdrawal.proof_value = proof_value
        withdrawal.reject_reason = reject_reason
        withdrawal.resolved_at = datetime.now(tz=UTC)
        await self._session.flush()
        return withdrawal


class PartnerBotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_tg_id: int,
        bot_id: int,
        username: str | None,
        token: str,
        markup_percent: Decimal,
    ) -> PartnerBot:
        """Insert or refresh a partner bot (idempotent on bot_id)."""
        stmt = (
            pg_insert(PartnerBot)
            .values(
                owner_tg_id=owner_tg_id,
                bot_id=bot_id,
                username=username,
                token=token,
                markup_percent=markup_percent,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=[PartnerBot.bot_id],
                set_={"username": username, "token": token, "active": True},
            )
        )
        await self._session.execute(stmt)
        return await self.get_by_bot_id(bot_id)  # type: ignore[return-value]

    async def get_by_bot_id(self, bot_id: int) -> PartnerBot | None:
        return await self._session.scalar(select(PartnerBot).where(PartnerBot.bot_id == bot_id))

    async def list_for_owner(self, owner_tg_id: int) -> list[PartnerBot]:
        result = await self._session.execute(
            select(PartnerBot)
            .where(PartnerBot.owner_tg_id == owner_tg_id)
            .order_by(PartnerBot.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_active(self) -> list[PartnerBot]:
        result = await self._session.execute(
            select(PartnerBot).where(PartnerBot.active.is_(True))
        )
        return list(result.scalars().all())

    async def set_markup(self, bot_id: int, markup_percent: Decimal) -> PartnerBot | None:
        bot = await self.get_by_bot_id(bot_id)
        if bot is None:
            return None
        bot.markup_percent = markup_percent
        await self._session.flush()
        return bot

    async def set_active(self, bot_id: int, active: bool) -> PartnerBot | None:
        bot = await self.get_by_bot_id(bot_id)
        if bot is None:
            return None
        bot.active = active
        await self._session.flush()
        return bot


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
        partner_owner_tg_id: int | None = None,
        partner_earning: Decimal = Decimal("0"),
        bot_id: int | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> Order:
        order = Order(
            order_id=order_id,
            buyer_tg_id=buyer_tg_id,
            target_username=target_username,
            count=count,
            amount=amount,
            status=status,
            partner_owner_tg_id=partner_owner_tg_id,
            partner_earning=partner_earning,
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
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

    async def buyer_stats(self, buyer_tg_id: int) -> dict[str, Any]:
        """A single buyer's paid totals: order count, stars bought, money spent."""
        paid = [OrderStatusEnum.PAID.value, OrderStatusEnum.SUCCESS.value]
        flt = (Order.buyer_tg_id == buyer_tg_id) & Order.status.in_(paid)
        orders = await self._session.scalar(select(func.count()).select_from(Order).where(flt))
        stars = await self._session.scalar(
            select(func.coalesce(func.sum(Order.count), 0)).where(flt)
        )
        spent = await self._session.scalar(
            select(func.coalesce(func.sum(Order.amount), 0)).where(flt)
        )
        return {"orders": int(orders or 0), "stars": int(stars or 0), "spent": Decimal(spent or 0)}

    async def period_stats(self, days: int) -> dict[str, Any]:
        """Paid totals over the last ``days`` days: orders, stars, revenue, margin."""
        paid = [OrderStatusEnum.PAID.value, OrderStatusEnum.SUCCESS.value]
        since = datetime.now(tz=UTC) - timedelta(days=days)
        flt = Order.status.in_(paid) & (Order.created_at >= since)
        orders = await self._session.scalar(select(func.count()).select_from(Order).where(flt))
        stars = await self._session.scalar(
            select(func.coalesce(func.sum(Order.count), 0)).where(flt)
        )
        revenue = await self._session.scalar(
            select(func.coalesce(func.sum(Order.amount), 0)).where(flt)
        )
        margin = await self._session.scalar(
            select(func.coalesce(func.sum(Order.margin), 0)).where(flt)
        )
        return {
            "orders": int(orders or 0),
            "stars": int(stars or 0),
            "revenue": Decimal(revenue or 0),
            "margin": Decimal(margin or 0),
        }

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
