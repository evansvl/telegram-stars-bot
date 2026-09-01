"""HTTP server for WATA webhook notifications.

Runs plain HTTP inside the container; TLS is terminated by a user-managed
reverse proxy (see README). The signature is verified with the WATA public
key; the order status is then re-fetched from WATA as the source of truth.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from aiogram import Bot
from aiohttp import web

from app.bot.notify import notify_order_outcome
from app.config import Settings
from app.db.models import OrderStatusEnum
from app.services import OrderService, PartnerService, ReferralService
from app.wata.signature import SignatureVerifier
from app.webhook.public_api import register_public_api

logger = logging.getLogger(__name__)

# i18n keys for buyer notifications, keyed by terminal order status.
_NOTIFY_KEYS = {
    OrderStatusEnum.PAID.value: "notify_Paid",
    OrderStatusEnum.SUCCESS.value: "notify_Success",
    OrderStatusEnum.REFUNDED.value: "notify_Refunded",
    OrderStatusEnum.FAIL.value: "notify_Fail",
}

_SUCCESS_STATUSES = {OrderStatusEnum.PAID.value, OrderStatusEnum.SUCCESS.value}


def _extract_order_id(payload: dict[str, Any]) -> str | None:
    for key in ("orderId", "order_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def _handle_webhook(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    verifier: SignatureVerifier = request.app["verifier"]
    service: OrderService = request.app["service"]
    referral: ReferralService = request.app["referral"]
    partner: PartnerService = request.app["partner"]
    bot: Bot = request.app["bot"]

    body = await request.read()

    # Optional operator-managed gate, in addition to the mandatory WATA signature.
    if settings.webhook_secret:
        provided = request.headers.get("X-Webhook-Secret", "")
        if not secrets.compare_digest(provided, settings.webhook_secret):
            logger.warning("webhook rejected: bad shared secret")
            return web.Response(status=403, text="forbidden")

    signature = request.headers.get("X-Signature")
    if not signature:
        logger.warning("webhook rejected: missing X-Signature")
        return web.Response(status=403, text="missing signature")
    if await verifier.public_key() is None:
        # A post-payment notification is retried by WATA after non-2xx responses.
        # Failing closed is more important than acknowledging an unverified payment.
        logger.error("webhook verification unavailable: WATA public key not loaded")
        return web.Response(status=503, text="verification unavailable")
    if not await verifier.verify(body, signature):
        logger.warning("webhook rejected: invalid X-Signature")
        return web.Response(status=403, text="bad signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.error("webhook: invalid JSON body")
        return web.Response(status=400, text="invalid json")
    if not isinstance(payload, dict):
        logger.error("webhook: JSON payload is not an object")
        return web.Response(status=400, text="invalid payload")

    order_id = _extract_order_id(payload)
    if not order_id:
        logger.warning("webhook without orderId; acknowledging")
        return web.Response(status=200, text="ok")
    logger.info("verified WATA webhook received order_id=%s", order_id)

    # WATA is the source of truth: re-fetch status (and auto-confirm if enabled).
    try:
        order = await service.sync_order(order_id, raw_webhook=payload)
    except Exception:
        logger.exception("WATA order verification failed order_id=%s", order_id)
        return web.Response(status=503, text="status verification failed")
    if order is None:
        logger.warning("webhook for unknown order order_id=%s", order_id)
        return web.Response(status=200, text="ok")

    notify_key = _NOTIFY_KEYS.get(order.status)
    if notify_key:
        # Notify the buyer (through the partner bot for partner orders) and credit
        # the referrer/partner — shared with the TON poller.
        await notify_order_outcome(
            bot=bot,
            service=service,
            referral=referral,
            partner=partner,
            order=order,
            success=order.status in _SUCCESS_STATUSES,
            notify_key=notify_key,
        )

    return web.Response(status=200, text="ok")


async def _health(_: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def build_webhook_app(
    *,
    settings: Settings,
    service: OrderService,
    referral: ReferralService,
    partner: PartnerService,
    bot: Bot,
    verifier: SignatureVerifier,
) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app["service"] = service
    app["referral"] = referral
    app["partner"] = partner
    app["bot"] = bot
    app["verifier"] = verifier
    app.router.add_post(settings.webhook_path, _handle_webhook)
    app.router.add_get("/health", _health)
    if settings.public_api_enabled:
        register_public_api(app)
    return app
