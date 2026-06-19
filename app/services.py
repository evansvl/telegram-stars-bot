"""Application services tying together WATA, pricing and the database."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

from app.bot.i18n import DEFAULT_LANG, normalize_lang
from app.config import Settings
from app.db.models import Order, OrderStatusEnum, Withdrawal, WithdrawalMethod, WithdrawalStatus
from app.db.repositories import (
    OrderRepository,
    ReferralRepository,
    UserRepository,
    WithdrawalRepository,
)
from app.db.session import Database
from app.pricing import PriceQuote, compute_amount, estimated_margin
from app.wata.client import WataClient
from app.wata.models import StarPrice

logger = logging.getLogger(__name__)

# WATA -> local status mapping (identity for the documented states).
_WATA_TO_LOCAL = {
    "Pending": OrderStatusEnum.PENDING,
    "Review": OrderStatusEnum.REVIEW,
    "Paid": OrderStatusEnum.PAID,
    "Success": OrderStatusEnum.SUCCESS,
    "Refunded": OrderStatusEnum.REFUNDED,
    "Fail": OrderStatusEnum.FAIL,
}


def map_status(wata_status: str) -> str:
    enum_val = _WATA_TO_LOCAL.get(wata_status)
    return enum_val.value if enum_val else (wata_status or OrderStatusEnum.PENDING.value)


@dataclass(slots=True)
class Quote:
    star_price: StarPrice
    quote: PriceQuote


@dataclass(slots=True)
class CreatedOrder:
    order_id: str
    payment_link: str
    amount: Decimal
    count: int
    target_username: str


class OrderService:
    def __init__(self, settings: Settings, wata: WataClient, db: Database) -> None:
        self._settings = settings
        self._wata = wata
        self._db = db
        self._lang_cache: dict[int, str] = {}

    @property
    def markup_percent(self) -> float:
        return self._settings.markup_percent

    # ── Language preferences ─────────────────────────────────────

    async def resolve_language(self, tg_id: int, telegram_language_code: str | None) -> str:
        """Return the user's language: stored choice, else Telegram locale, else default."""
        cached = self._lang_cache.get(tg_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            stored = await UserRepository(session).get_language(tg_id)
        lang = stored or normalize_lang(telegram_language_code)
        self._lang_cache[tg_id] = lang
        return lang

    async def set_language(self, tg_id: int, language: str) -> None:
        lang = normalize_lang(language)
        async with self._db.session() as session:
            await UserRepository(session).set_language(tg_id, lang)
        self._lang_cache[tg_id] = lang

    async def get_user_language(self, tg_id: int) -> str:
        """Best-effort language lookup for out-of-band messages (e.g. webhooks)."""
        cached = self._lang_cache.get(tg_id)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            stored = await UserRepository(session).get_language(tg_id)
        lang = stored or DEFAULT_LANG
        self._lang_cache[tg_id] = lang
        return lang

    async def get_star_price(self, username: str) -> StarPrice:
        """Validate a Telegram username and return WATA prices (raises WataApiError)."""
        return await self._wata.get_star_price(username)

    async def quote(self, username: str, count: int) -> Quote:
        """Validate the username via WATA and compute the payable amount."""
        price = await self._wata.get_star_price(username)
        quote = compute_amount(price.min_price, count, self._settings.markup_percent)
        return Quote(star_price=price, quote=quote)

    async def create_order(
        self,
        *,
        buyer_tg_id: int,
        buyer_username: str | None,
        target_username: str,
        count: int,
        amount: Decimal,
    ) -> CreatedOrder:
        """Create a WATA order and persist it. Returns the payment link."""
        order_id = uuid.uuid4().hex

        async with self._db.session() as session:
            users = UserRepository(session)
            orders = OrderRepository(session)
            await users.upsert(buyer_tg_id, buyer_username)
            await orders.create(
                order_id=order_id,
                buyer_tg_id=buyer_tg_id,
                target_username=target_username,
                count=count,
                amount=amount,
                status=OrderStatusEnum.NEW.value,
            )

        try:
            result = await self._wata.create_order(
                username=target_username,
                count=count,
                amount=amount,
                order_id=order_id,
                description=f"Telegram Stars x{count} for @{target_username}",
                telegram_id=buyer_tg_id,
            )
        except Exception:
            async with self._db.session() as session:
                await OrderRepository(session).update_status(
                    order_id, status=OrderStatusEnum.ERROR.value
                )
            raise

        margin = estimated_margin(amount, result.price, result.commission)
        async with self._db.session() as session:
            await OrderRepository(session).update_status(
                order_id,
                status=OrderStatusEnum.PENDING.value,
                price=result.price,
                commission=result.commission,
                margin=margin,
                payment_link=result.payment_link,
            )

        logger.info(
            "order created order_id=%s count=%d amount=%s margin=%s",
            order_id,
            count,
            amount,
            margin,
        )
        return CreatedOrder(
            order_id=order_id,
            payment_link=result.payment_link,
            amount=amount,
            count=count,
            target_username=target_username,
        )

    async def sync_order(
        self, order_id: str, raw_webhook: dict[str, Any] | None = None
    ) -> Order | None:
        """Fetch authoritative status from WATA, auto-confirm if configured, persist.

        Used both by the webhook handler and the manual "check payment" button.
        Returns the updated Order (or None if unknown).
        """
        try:
            status = await self._wata.get_order(order_id)
        except Exception:
            logger.exception("failed to fetch order status order_id=%s", order_id)
            return await self._get_local(order_id)

        wata_status = status.status

        # Auto-confirm: Review -> Paid (release the stars).
        if wata_status == "Review" and self._settings.auto_confirm:
            try:
                confirmed = await self._wata.confirm_order(order_id)
                wata_status = confirmed.status or "Paid"
                logger.info("order auto-confirmed order_id=%s status=%s", order_id, wata_status)
            except Exception:
                logger.exception("auto-confirm failed order_id=%s", order_id)

        margin = (
            estimated_margin(status.amount, status.price, status.commission)
            if status.amount
            else None
        )
        async with self._db.session() as session:
            updated = await OrderRepository(session).update_status(
                order_id,
                status=map_status(wata_status),
                price=status.price or None,
                commission=status.commission or None,
                margin=margin,
                raw_webhook=raw_webhook,
            )
        return updated

    async def list_orders(self, buyer_tg_id: int, limit: int = 20) -> list[Order]:
        async with self._db.session() as session:
            return await OrderRepository(session).list_for_buyer(buyer_tg_id, limit=limit)

    async def stats(self) -> dict[str, Any]:
        async with self._db.session() as session:
            return await OrderRepository(session).stats()

    async def _get_local(self, order_id: str) -> Order | None:
        async with self._db.session() as session:
            return await OrderRepository(session).get_by_order_id(order_id)


# ── Referral program ─────────────────────────────────────────────

_CREDIT_STATUSES = {OrderStatusEnum.PAID.value, OrderStatusEnum.SUCCESS.value}


class WithdrawalError(Exception):
    """Raised when a withdrawal request fails validation; ``key`` is an i18n key."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


@dataclass(slots=True)
class ReferralOverview:
    link: str
    referrals: int
    earned: Decimal
    available: Decimal
    has_pending: bool


class ReferralService:
    def __init__(self, settings: Settings, db: Database, *, bot_username: str = "") -> None:
        self._settings = settings
        self._db = db
        self.bot_username = bot_username

    @property
    def percent(self) -> float:
        return self._settings.referral_percent

    def referral_link(self, tg_id: int) -> str:
        return f"https://t.me/{self.bot_username}?start=ref_{tg_id}"

    async def register_referral(self, new_tg_id: int, referrer_tg_id: int) -> bool:
        """Attribute a new user to a referrer (once, never self). Returns True if applied."""
        if new_tg_id == referrer_tg_id:
            return False
        async with self._db.session() as session:
            return await UserRepository(session).set_referrer(new_tg_id, referrer_tg_id)

    async def credit_for_order(self, order: Order) -> tuple[int, Decimal] | None:
        """Credit the buyer's referrer 5% of a paid order. Idempotent per order.

        Returns (referrer_tg_id, amount) only when a new credit was recorded.
        """
        if order.status not in _CREDIT_STATUSES:
            return None
        percent = Decimal(str(self._settings.referral_percent))
        amount = (order.amount * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        if amount <= 0:
            return None
        async with self._db.session() as session:
            referrer = await UserRepository(session).get_referrer(order.buyer_tg_id)
            if not referrer:
                return None
            inserted = await ReferralRepository(session).credit(
                referrer_tg_id=referrer,
                referred_tg_id=order.buyer_tg_id,
                order_id=order.order_id,
                amount=amount,
            )
        if not inserted:
            return None
        logger.info(
            "referral credited referrer=%s order_id=%s amount=%s",
            referrer,
            order.order_id,
            amount,
        )
        return referrer, amount

    async def overview(self, tg_id: int) -> ReferralOverview:
        async with self._db.session() as session:
            referrals = await UserRepository(session).count_referrals(tg_id)
            earned = await ReferralRepository(session).total_earned(tg_id)
            committed = await WithdrawalRepository(session).committed_total(tg_id)
            has_pending = await WithdrawalRepository(session).has_pending(tg_id)
        return ReferralOverview(
            link=self.referral_link(tg_id),
            referrals=referrals,
            earned=earned,
            available=earned - committed,
            has_pending=has_pending,
        )

    def min_for_method(self, method: str) -> Decimal:
        if method == WithdrawalMethod.CRYPTO.value:
            return self._settings.withdraw_min_crypto
        return self._settings.withdraw_min_sbp

    async def create_withdrawal(
        self, *, tg_id: int, method: str, destination: str, amount: Decimal
    ) -> Withdrawal:
        """Validate against balance/minimums and create a pending request."""
        overview = await self.overview(tg_id)
        if overview.has_pending:
            raise WithdrawalError("wd_has_pending")
        minimum = self.min_for_method(method)
        if amount < minimum:
            raise WithdrawalError("wd_below_min", min=minimum)
        if amount > overview.available:
            raise WithdrawalError("wd_over_balance", available=overview.available)
        async with self._db.session() as session:
            return await WithdrawalRepository(session).create(
                user_tg_id=tg_id, amount=amount, method=method, destination=destination
            )

    async def list_withdrawals(self, tg_id: int, limit: int = 20) -> list[Withdrawal]:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).list_for_user(tg_id, limit=limit)

    async def get_withdrawal(self, withdrawal_id: int) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).get(withdrawal_id)

    async def approve_withdrawal(
        self, withdrawal_id: int, *, admin_id: int, proof_type: str, proof_value: str
    ) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).resolve(
                withdrawal_id,
                status=WithdrawalStatus.APPROVED.value,
                resolved_by=admin_id,
                proof_type=proof_type,
                proof_value=proof_value,
            )

    async def reject_withdrawal(
        self, withdrawal_id: int, *, admin_id: int, reason: str
    ) -> Withdrawal | None:
        async with self._db.session() as session:
            return await WithdrawalRepository(session).resolve(
                withdrawal_id,
                status=WithdrawalStatus.REJECTED.value,
                resolved_by=admin_id,
                reject_reason=reason,
            )
