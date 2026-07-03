"""Background task that turns confirmed TON deposits into delivered stars.

Webhook-only deployment has no long-poll loop, so this runs as an asyncio task
started/stopped in ``app/main.py``. Each tick it asks the scanner for new
incoming transfers, matches each to a pending crypto order by its exact
(jittered) TON amount, delivers via GGP ``buyStars`` and notifies the buyer.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from redis.asyncio import Redis

from app.bot.notify import notify_order_outcome
from app.config import Settings
from app.db.models import Order, OrderStatusEnum
from app.services import OrderService, PartnerService, ReferralService
from app.ton.scanner import TonScanner

logger = logging.getLogger(__name__)

_CURSOR_KEY = "ton:last_lt"


class TonPoller:
    def __init__(
        self,
        *,
        settings: Settings,
        scanner: TonScanner,
        service: OrderService,
        referral: ReferralService,
        partner: PartnerService,
        bot: Bot,
        redis: Redis,
    ) -> None:
        self._settings = settings
        self._scanner = scanner
        self._service = service
        self._referral = referral
        self._partner = partner
        self._bot = bot
        self._redis = redis

    async def run(self) -> None:
        interval = max(5, self._settings.ton_poll_interval)
        logger.info("TON poller started (interval=%ds)", interval)
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("TON poller tick failed")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("TON poller stopped")
            raise

    async def _tick(self) -> None:
        since = await self._get_cursor()
        transfers = await self._scanner.incoming_transfers(since)
        if not transfers:
            return
        max_lt = since
        for tr in transfers:
            max_lt = max(max_lt, tr.lt)
            order = await self._service.match_crypto_payment(tr.value_ton)
            if order is None:
                continue
            logger.info(
                "TON payment matched order_id=%s amount=%s lt=%d",
                order.order_id,
                tr.value_ton,
                tr.lt,
            )
            await self._deliver(order)
        await self._set_cursor(max_lt)

    async def _deliver(self, order: Order) -> None:
        delivered = await self._service.deliver_ggp_order(order.order_id)
        if delivered is None:
            return
        success = delivered.status == OrderStatusEnum.SUCCESS.value
        notify_key = "notify_Success" if success else "crypto_failed_contact"
        await notify_order_outcome(
            bot=self._bot,
            service=self._service,
            referral=self._referral,
            partner=self._partner,
            order=delivered,
            success=success,
            notify_key=notify_key,
        )

    async def _get_cursor(self) -> int:
        try:
            raw = await self._redis.get(_CURSOR_KEY)
            return int(raw) if raw else 0
        except Exception:
            logger.exception("failed to read TON cursor")
            return 0

    async def _set_cursor(self, lt: int) -> None:
        try:
            await self._redis.set(_CURSOR_KEY, str(lt))
        except Exception:
            logger.exception("failed to persist TON cursor")
