"""HTTP server for WATA webhook notifications.

Runs plain HTTP inside the container; TLS is terminated by a user-managed
reverse proxy (see README). The signature is verified with the WATA public
key; the order status is then re-fetched from WATA as the source of truth.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import Bot
from aiohttp import web

from app.bot.i18n import t
from app.config import Settings
from app.db.models import OrderStatusEnum
from app.services import OrderService
from app.wata.signature import SignatureVerifier

logger = logging.getLogger(__name__)

# i18n keys for buyer notifications, keyed by terminal order status.
_NOTIFY_KEYS = {
    OrderStatusEnum.PAID.value: "notify_Paid",
    OrderStatusEnum.SUCCESS.value: "notify_Success",
    OrderStatusEnum.REFUNDED.value: "notify_Refunded",
    OrderStatusEnum.FAIL.value: "notify_Fail",
}


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
    bot: Bot = request.app["bot"]

    body = await request.read()

    # Optional shared-secret gate (header), in addition to RSA signature.
    if settings.webhook_secret:
        provided = request.headers.get("X-Webhook-Secret", "")
        if provided != settings.webhook_secret:
            logger.warning("webhook rejected: bad shared secret")
            return web.Response(status=403, text="forbidden")

    signature = request.headers.get("X-Signature")
    verified = await verifier.verify(body, signature)
    if not verified:
        # Don't hard-fail if we couldn't load the key — log and continue, since
        # we re-verify the truth via GET /stars/order. But reject if a key exists
        # and the signature is clearly invalid.
        if await verifier.public_key() is not None and signature:
            logger.warning("webhook rejected: invalid X-Signature")
            return web.Response(status=403, text="bad signature")
        logger.warning("webhook signature not verified (continuing via status re-fetch)")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.error("webhook: invalid JSON body")
        return web.Response(status=400, text="invalid json")

    logger.info("webhook received payload=%s", payload)

    order_id = _extract_order_id(payload)
    if not order_id:
        logger.warning("webhook without orderId; acknowledging")
        return web.Response(status=200, text="ok")

    # WATA is the source of truth: re-fetch status (and auto-confirm if enabled).
    order = await service.sync_order(order_id, raw_webhook=payload)
    if order is None:
        logger.warning("webhook for unknown order order_id=%s", order_id)
        return web.Response(status=200, text="ok")

    notify_key = _NOTIFY_KEYS.get(order.status)
    if notify_key:
        lang = await service.get_user_language(order.buyer_tg_id)
        try:
            await bot.send_message(order.buyer_tg_id, t(notify_key, lang))
        except Exception:
            logger.exception("failed to notify buyer tg_id=%s", order.buyer_tg_id)

    return web.Response(status=200, text="ok")


async def _health(_: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def build_webhook_app(
    *,
    settings: Settings,
    service: OrderService,
    bot: Bot,
    verifier: SignatureVerifier,
) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app["service"] = service
    app["bot"] = bot
    app["verifier"] = verifier
    app.router.add_post(settings.webhook_path, _handle_webhook)
    app.router.add_get("/health", _health)
    return app
