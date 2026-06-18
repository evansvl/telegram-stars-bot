"""Application services tying together WATA, pricing and the database."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.db.models import Order, OrderStatusEnum
from app.db.repositories import OrderRepository, UserRepository
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

    @property
    def markup_percent(self) -> float:
        return self._settings.markup_percent

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
