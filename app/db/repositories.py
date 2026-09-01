"""Data-access repositories for users and orders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import (
    Order,
    OrderStatusEnum,
    PartnerBot,
    ReferralEarning,
    User,
    Withdrawal,
    WithdrawalStatus,
)

# Serialize GGP delivery claims across every bot process.  A process-local asyncio
# lock would not protect multi-worker deployments; PostgreSQL transaction advisory
# locks are released automatically on commit/rollback (including process crashes).
# Keep this in signed-int32 range so asyncpg selects PostgreSQL's one-argument
# bigint overload without first trying to encode an out-of-range int4 literal.
_GGP_CLAIM_LOCK_ID = 0x47475043  # ASCII: "GGPC"

# Serialize payment-proof matches across processes. Besides the database-wide unique
# constraint on tx hash, this makes the "unused hash" check return a clean no-op under
# a duplicate concurrent notification instead of surfacing an IntegrityError.
_TON_PAYMENT_MATCH_LOCK_ID = 0x544F4E4D  # ASCII: "TONM"


def _delivered_order_filter() -> ColumnElement[bool]:
    """SQL predicate for genuinely delivered orders across provider lifecycles."""
    return or_(
        Order.status == OrderStatusEnum.SUCCESS.value,
        and_(
            Order.provider != "ggp",
            Order.status == OrderStatusEnum.PAID.value,
        ),
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

    async def count(self) -> int:
        return int(await self._session.scalar(select(func.count()).select_from(User)) or 0)

    async def recent(self, limit: int = 6) -> list[User]:
        """The most recently registered users."""
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_page(self, offset: int, limit: int) -> list[User]:
        """A page of users, newest first."""
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def find_by_username(self, username: str) -> User | None:
        """Look a user up by @username (case-insensitive), without the leading @."""
        return await self._session.scalar(
            select(User).where(func.lower(User.username) == username.lstrip("@").lower())
        )

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

    async def total_credited(self) -> Decimal:
        """Sum of all referral/partner earnings ever credited, across all users."""
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ReferralEarning.amount), 0))
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

    async def total_approved(self) -> Decimal:
        """Sum of all paid-out (approved) withdrawals, across all users."""
        total = await self._session.scalar(
            select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
                Withdrawal.status == WithdrawalStatus.APPROVED.value
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
        result = await self._session.execute(select(PartnerBot).where(PartnerBot.active.is_(True)))
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

    async def delete(self, bot_id: int) -> bool:
        """Remove a partner bot row. Returns True if one was deleted."""
        bot = await self.get_by_bot_id(bot_id)
        if bot is None:
            return False
        await self._session.delete(bot)
        await self._session.flush()
        return True


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
        provider: str = "wata",
        ton_amount: Decimal | None = None,
        payment_expires_at: datetime | None = None,
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
            provider=provider,
            ton_amount=ton_amount,
            payment_expires_at=payment_expires_at,
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

    async def get_by_order_id_for_update(self, order_id: str) -> Order | None:
        """Load an order under a row lock until the surrounding transaction ends."""
        result = await self._session.execute(
            select(Order).where(Order.order_id == order_id).with_for_update()
        )
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
        ggp_tx_id: str | None = None,
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
        if ggp_tx_id is not None:
            order.ggp_tx_id = ggp_tx_id
        if raw_webhook is not None:
            order.raw_webhook = raw_webhook
        await self._session.flush()
        return order

    async def outstanding_ton_amounts(self) -> set[Decimal]:
        """Every TON invoice amount ever issued (for permanent non-reuse).

        Address + shared GGP memo + amount are the only available invoice identity.
        Reusing an expired amount could let a late old transfer satisfy a new order,
        so terminal and legacy amounts remain reserved as well.
        """
        result = await self._session.execute(
            select(Order.ton_amount).where(
                Order.provider == "ggp",
                Order.ton_amount.is_not(None),
            )
        )
        return {row for row in result.scalars().all() if row is not None}

    async def oldest_pending_crypto_created_at(self) -> datetime | None:
        """Creation time of the oldest GGP order still awaiting an on-chain payment."""
        return await self._session.scalar(
            select(Order.created_at)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PENDING.value,
            )
            .order_by(Order.created_at.asc(), Order.id.asc())
            .limit(1)
        )

    async def match_pending_crypto_payment(
        self,
        *,
        ton_amount: Decimal,
        tx_hash: str,
        tx_lt: int,
        tx_utime: datetime,
        reconciliation_grace_minutes: int = 0,
        order_id: str | None = None,
    ) -> Order | None:
        """Atomically bind an unused on-chain payment to its exact pending order.

        The transaction timestamp must fall within the order-specific payment window
        plus a short reconciliation grace.  That grace absorbs the race where a
        transfer was already broadcast when the visible deadline/cancel action
        closed the invoice, but Toncenter records its block a little later. A matched
        order moves PENDING -> REVIEW and owns the transaction hash forever.
        """
        if not tx_hash:
            raise ValueError("TON payment transaction hash must not be empty")

        await self._session.execute(select(func.pg_advisory_xact_lock(_TON_PAYMENT_MATCH_LOCK_ID)))
        used_payment = Order.__table__.alias("used_ton_payment")
        unused_tx_hash = ~(
            select(used_payment.c.id).where(used_payment.c.ton_payment_tx_hash == tx_hash).exists()
        )
        # TON exposes a block timestamp rather than the wallet's local broadcast
        # time. Accepting the same bounded grace used by expiration avoids taking a
        # real payment without fulfilling it. Invoice amounts are never reused, so
        # this cannot make an old transfer satisfy a different order.
        latest_stored_deadline = tx_utime - timedelta(minutes=max(0, reconciliation_grace_minutes))
        conditions = [
            Order.provider == "ggp",
            Order.status == OrderStatusEnum.PENDING.value,
            Order.ton_amount == ton_amount,
            Order.ton_payment_tx_hash.is_(None),
            Order.payment_expires_at.is_not(None),
            # TON utime has whole-second precision, while PostgreSQL created_at
            # commonly carries microseconds. Floor the lower boundary so a valid
            # payment in the order's creation second is not rejected.
            func.date_trunc("second", Order.created_at) <= tx_utime,
            Order.payment_expires_at >= latest_stored_deadline,
            unused_tx_hash,
        ]
        if order_id is not None:
            conditions.append(Order.order_id == order_id)
        result = await self._session.execute(
            update(Order)
            .where(*conditions)
            .values(
                status=OrderStatusEnum.REVIEW.value,
                ton_payment_tx_hash=tx_hash,
                ton_payment_lt=tx_lt,
                ton_payment_utime=tx_utime,
            )
            .returning(Order.order_id)
        )
        order_id = result.scalar_one_or_none()
        if order_id is None:
            return None
        return await self.get_by_order_id(order_id)

    async def oldest_verified_crypto(self) -> Order | None:
        """Oldest verified GGP order awaiting delivery (the global FIFO head)."""
        result = await self._session.execute(
            select(Order)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.REVIEW.value,
                Order.ton_payment_tx_hash.is_not(None),
                Order.ton_payment_lt.is_not(None),
                Order.ton_payment_utime.is_not(None),
            )
            .order_by(Order.created_at.asc(), Order.id.asc())
            .limit(1)
        )
        return result.scalars().first()

    async def claim_verified_crypto(
        self, order_id: str, *, buyer_tg_id: int | None = None
    ) -> Order | None:
        """Claim only the verified FIFO head, with one GGP delivery in flight.

        The transaction-scoped PostgreSQL advisory lock serializes claims across
        processes.  The persisted PAID state then acts as a lease that deliberately
        blocks later orders while ``buyStars`` is running. Only an order whose TON
        payment has already moved it to REVIEW can be claimed for delivery.
        """
        await self._session.execute(select(func.pg_advisory_xact_lock(_GGP_CLAIM_LOCK_ID)))

        in_flight = await self._session.scalar(
            select(Order.id)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PAID.value,
            )
            .limit(1)
        )
        if in_flight is not None:
            return None

        oldest_id = await self._session.scalar(
            select(Order.id)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.REVIEW.value,
                Order.ton_payment_tx_hash.is_not(None),
                Order.ton_payment_lt.is_not(None),
                Order.ton_payment_utime.is_not(None),
            )
            .order_by(Order.created_at.asc(), Order.id.asc())
            .limit(1)
        )
        if oldest_id is None:
            return None

        conditions = [
            Order.id == oldest_id,
            Order.order_id == order_id,
            Order.provider == "ggp",
            Order.status == OrderStatusEnum.REVIEW.value,
            Order.ton_payment_tx_hash.is_not(None),
            Order.ton_payment_lt.is_not(None),
            Order.ton_payment_utime.is_not(None),
        ]
        if buyer_tg_id is not None:
            conditions.append(Order.buyer_tg_id == buyer_tg_id)
        result = await self._session.execute(select(Order).where(*conditions).with_for_update())
        order = result.scalar_one_or_none()
        if order is None:
            return None
        order.status = OrderStatusEnum.PAID.value
        await self._session.flush()
        return order

    async def finish_crypto_claim(
        self,
        order_id: str,
        *,
        status: str,
        ggp_tx_id: str | None = None,
    ) -> Order | None:
        """Finish or release a GGP claim only while it remains in PAID state.

        REVIEW releases a claim after an insufficient provider balance. SUCCESS and
        ERROR are terminal delivery outcomes. A successful delivery must carry the
        provider transaction id used later as credit proof.
        """
        allowed = {
            OrderStatusEnum.REVIEW.value,
            OrderStatusEnum.SUCCESS.value,
            OrderStatusEnum.ERROR.value,
        }
        if status not in allowed:
            raise ValueError(f"invalid GGP claim outcome: {status}")
        if status == OrderStatusEnum.SUCCESS.value and not ggp_tx_id:
            raise ValueError("successful GGP delivery requires ggp_tx_id")
        values: dict[str, Any] = {"status": status}
        if ggp_tx_id is not None:
            values["ggp_tx_id"] = ggp_tx_id
        result = await self._session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PAID.value,
            )
            .values(**values)
            .returning(Order.order_id)
        )
        finished_order_id = result.scalar_one_or_none()
        if finished_order_id is None:
            return None
        return await self.get_by_order_id(finished_order_id)

    async def fail_stale_crypto_claims(self, older_than_minutes: int) -> int:
        """Quarantine abandoned GGP claims without risking a duplicate delivery.

        A PAID row may mean ``buyStars`` succeeded but the process died before the
        final DB write.  It is therefore unsafe to put such a row back into the
        retry queue automatically; ERROR requires operator reconciliation instead.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=older_than_minutes)
        result = await self._session.execute(
            update(Order)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PAID.value,
                Order.updated_at < cutoff,
            )
            .values(status=OrderStatusEnum.ERROR.value)
        )
        return result.rowcount or 0

    async def pending_crypto_order(self, buyer_tg_id: int) -> Order | None:
        """The buyer's active crypto order, if any (one-at-a-time guard)."""
        result = await self._session.execute(
            select(Order)
            .where(
                Order.buyer_tg_id == buyer_tg_id,
                Order.provider == "ggp",
                Order.status.in_(
                    [
                        OrderStatusEnum.PENDING.value,
                        OrderStatusEnum.REVIEW.value,
                        OrderStatusEnum.PAID.value,
                    ]
                ),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def expire_stale_crypto(self, reconciliation_grace_minutes: int = 0) -> int:
        """Expire only after deadline + indexer/finality reconciliation grace."""
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=max(0, reconciliation_grace_minutes))
        result = await self._session.execute(
            update(Order)
            .where(
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PENDING.value,
                Order.payment_expires_at.is_not(None),
                Order.payment_expires_at <= cutoff,
            )
            .values(status=OrderStatusEnum.EXPIRED.value)
        )
        return result.rowcount or 0

    async def cancel_pending_crypto(self, order_id: str, buyer_tg_id: int) -> bool:
        """Close the payment window, retaining a grace period for in-flight TON."""
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.buyer_tg_id == buyer_tg_id,
                Order.provider == "ggp",
                Order.status == OrderStatusEnum.PENDING.value,
                Order.payment_expires_at.is_not(None),
                Order.payment_expires_at > now,
            )
            .values(payment_expires_at=now)
        )
        return (result.rowcount or 0) > 0

    async def count_active(self, buyer_tg_id: int, within_minutes: int = 60) -> int:
        """Recent unpaid orders for a buyer (matches WATA's concurrent-order limit).

        Bounded to a recent window so WATA-expired links don't block forever even
        if we never received an expiry webhook for them.
        """
        active = [OrderStatusEnum.NEW.value, OrderStatusEnum.PENDING.value]
        since = datetime.now(tz=UTC) - timedelta(minutes=within_minutes)
        count = await self._session.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.buyer_tg_id == buyer_tg_id,
                Order.status.in_(active),
                Order.created_at >= since,
            )
        )
        return int(count or 0)

    async def buyer_stats(self, buyer_tg_id: int) -> dict[str, Any]:
        """A single buyer's paid totals: order count, stars bought, money spent."""
        flt = and_(Order.buyer_tg_id == buyer_tg_id, _delivered_order_filter())
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
        since = datetime.now(tz=UTC) - timedelta(days=days)
        flt = and_(_delivered_order_filter(), Order.created_at >= since)
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
        total_orders = await self._session.scalar(select(func.count()).select_from(Order))
        paid_filter = _delivered_order_filter()
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
