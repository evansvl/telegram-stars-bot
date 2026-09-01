"""Background worker that delivers crypto orders once the GGP balance covers them.

Webhook-only deployment has no long-poll loop, so this runs as an asyncio task
started/stopped in ``app/main.py``. Delivery is gated on the GGP balance rather
than on scanning the chain: the operator always receives the TON, so each tick we
take the oldest unpaid crypto order and, if ``check_balance`` shows funds, run
``buyStars`` to deliver it (FIFO). The buyer can also trigger a check immediately
via the "I paid" button.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.bot.notify import notify_order_outcome
from app.config import Settings
from app.db.models import Order
from app.ggp.errors import GgpError
from app.services import OrderService, PartnerService, ReferralService

logger = logging.getLogger(__name__)


class CryptoDeliveryWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        service: OrderService,
        referral: ReferralService,
        partner: PartnerService,
        bot: Bot,
    ) -> None:
        self._settings = settings
        self._service = service
        self._referral = referral
        self._partner = partner
        self._bot = bot

    async def run(self) -> None:
        interval = max(5, self._settings.ton_poll_interval)
        logger.info("crypto delivery worker started (interval=%ds)", interval)
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("crypto delivery tick failed")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("crypto delivery worker stopped")
            raise

    async def _tick(self) -> None:
        # A crash during the non-idempotent buyStars call leaves an intentionally
        # ambiguous PAID claim. Never retry it automatically: quarantine it for
        # operator reconciliation, avoiding a possible duplicate delivery.
        stale_claims = await self._service.fail_stale_crypto_claims()
        if stale_claims:
            logger.error(
                "quarantined %d stale GGP delivery claim(s) for manual reconciliation",
                stale_claims,
            )

        # Auto-cancel orders left unpaid past the TTL first: frees their TON amount
        # and the buyer's one-order slot, and keeps the FIFO queue from clogging.
        expired = await self._service.expire_stale_crypto()
        if expired:
            logger.info("expired %d unpaid crypto order(s)", expired)

        order = await self._service.oldest_pending_crypto()
        if order is None:
            return
        try:
            delivered, outcome = await self._service.try_deliver_crypto(order.order_id)
        except GgpError:
            logger.warning("GGP unavailable; will retry crypto delivery next tick")
            return
        if outcome == "delivered" and delivered is not None:
            await self._notify(delivered, success=True)
        elif outcome == "failed" and delivered is not None:
            await self._notify(delivered, success=False)
        # "not_funded" / "gone": nothing to do — retry on the next tick.

    async def _notify(self, order: Order, *, success: bool) -> None:
        await notify_order_outcome(
            bot=self._bot,
            service=self._service,
            referral=self._referral,
            partner=self._partner,
            order=order,
            success=success,
            notify_key="notify_Success" if success else "crypto_failed_contact",
        )
