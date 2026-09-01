"""Background worker for verified TON payments and GGP Stars delivery.

Webhook-only deployment has no long-poll loop, so this runs as an asyncio task
started/stopped in ``app/main.py``. Each tick first scans finalized TON transactions
and atomically binds exact address/amount/memo/time/hash proof to an order. Only
verified orders then enter the serialized FIFO ``buyStars`` delivery queue. The
buyer can trigger the same verification immediately via the "I paid" button.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.bot.notify import notify_crypto_payment_verified, notify_order_outcome
from app.config import Settings
from app.db.models import Order, OrderStatusEnum
from app.ggp.errors import GgpError
from app.services import OrderService, PartnerService, ReferralService
from app.ton.scanner import TonScannerError

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

        # Scan before expiring anything. If the indexer is unavailable, leave every
        # order untouched so a payment close to its deadline cannot be skipped.
        scan_ok = True
        try:
            matched_orders = await self._service.scan_crypto_payments()
        except TonScannerError as exc:
            logger.warning("TON scanner unavailable; payment queue unchanged: %s", exc)
            scan_ok = False
            matched_orders = []
        if matched_orders:
            logger.info("matched %d finalized TON payment(s)", len(matched_orders))
            for matched in matched_orders:
                await notify_crypto_payment_verified(
                    bot=self._bot,
                    service=self._service,
                    partner=self._partner,
                    order=matched,
                )

        # Only still-unverified PENDING orders can expire. REVIEW payments retain
        # their immutable chain proof and stay queued until provider delivery.
        if scan_ok:
            expired = await self._service.expire_stale_crypto()
            if expired:
                logger.info("expired %d unpaid crypto order(s)", expired)

        # Drain a bounded batch of verified orders. The repository serializes the
        # non-idempotent GGP call across workers/processes and enforces FIFO.
        for _ in range(50):
            order = await self._service.oldest_verified_crypto()
            if order is None:
                return
            try:
                delivered = await self._service.deliver_ggp_order(order.order_id)
            except GgpError:
                logger.warning("GGP unavailable; will retry verified delivery next tick")
                return
            if delivered is None:
                return
            if delivered.status == OrderStatusEnum.SUCCESS.value:
                await self._notify(delivered, success=True)
                continue
            if delivered.status == OrderStatusEnum.ERROR.value:
                await self._notify(delivered, success=False)
                continue
            # REVIEW means the buyer payment is proven but the provider balance is
            # not ready. Keep the proof and retry on the next tick.
            return

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
