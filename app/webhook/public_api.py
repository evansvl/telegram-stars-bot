"""Public web API for a website widget (e.g. a "buy stars" tab on your site).

Off by default. It turns on when ``PUBLIC_API_ALLOWED_ORIGINS`` is set, and only
accepts browser requests whose ``Origin`` matches that allowlist (plus CORS locked
to those origins) — that is what restricts it to *your* site for a client-side
widget. An optional IP allowlist and a per-IP rate limit are extra gates.

Endpoints (all under ``/api/public``):
  * ``GET  /config``          — which rails are on, limits, presets, display rates
  * ``POST /order``           — create an order, return payment details
  * ``GET  /order/{id}``      — poll an order's status

Web orders have no Telegram buyer, so they are stored under a sentinel buyer id
and neither notify a buyer nor pay a referrer/partner. Stars still reach the
recipient (``username``): WATA delivers on payment, GGP on balance (buyStars).
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque

from aiohttp import web

from app.config import Settings
from app.db.models import OrderStatusEnum
from app.ggp.errors import GgpError
from app.pricing import MAX_STARS, MIN_STARS, compute_amount, is_valid_count
from app.services import OrderService
from app.wata.errors import WataError

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_PRESETS = [50, 100, 250, 500, 1000]

# Web orders have no Telegram user; store them under this sentinel buyer id.
_WEB_BUYER_ID = 0
_WEB_BUYER_USERNAME = "web"

_DELIVERED_STATUSES = {OrderStatusEnum.PAID.value, OrderStatusEnum.SUCCESS.value}
_FAILED_STATUSES = {OrderStatusEnum.FAIL.value, OrderStatusEnum.ERROR.value,
                    OrderStatusEnum.REFUNDED.value}

# Per-IP sliding-window rate limiter (single process, in-memory).
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: web.Request) -> str:
    """Best-effort client IP, honouring a reverse proxy's X-Forwarded-For."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote or ""


def _rate_limited(ip: str, limit: int) -> bool:
    now = time.monotonic()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _allowed_origin(request: web.Request, settings: Settings) -> str | None:
    """The request's Origin if it is in the allowlist, else None."""
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and origin in settings.public_api_allowed_origins:
        return origin
    return None


@web.middleware
async def public_api_middleware(request: web.Request, handler):
    """Gate + CORS for /api/public only; passes everything else straight through."""
    if not request.path.startswith("/api/public"):
        return await handler(request)

    settings: Settings = request.app["settings"]
    if not settings.public_api_enabled:
        return web.json_response({"error": "disabled"}, status=404)

    origin = _allowed_origin(request, settings)

    # CORS preflight — answer before any auth so browsers get a clean 204.
    if request.method == "OPTIONS":
        if origin is None:
            return web.Response(status=403)
        return web.Response(status=204, headers=_cors_headers(origin))

    if origin is None:
        return web.json_response({"error": "forbidden_origin"}, status=403)

    allowed_ips = settings.public_api_allowed_ips
    if allowed_ips and _client_ip(request) not in allowed_ips:
        return web.json_response({"error": "forbidden_ip"}, status=403)

    if _rate_limited(_client_ip(request), settings.public_api_rate_limit):
        return web.json_response({"error": "rate_limited"}, status=429)

    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(_cors_headers(origin))
        raise
    response.headers.update(_cors_headers(origin))
    return response


async def _config(request: web.Request) -> web.Response:
    service: OrderService = request.app["service"]
    card_rate = await service.star_rate(service.markup_percent) if service.wata_enabled else None
    ton_rate = await service.crypto_star_rate()
    return web.json_response(
        {
            "methods": {"card": service.wata_enabled, "ton": service.crypto_enabled},
            "min_stars": MIN_STARS,
            "max_stars": MAX_STARS,
            "presets": _PRESETS,
            "rate_card_rub": str(card_rate) if card_rate is not None else None,
            "rate_ton_rub": str(ton_rate) if ton_rate is not None else None,
        }
    )


async def _create_order(request: web.Request) -> web.Response:
    service: OrderService = request.app["service"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    username = str(body.get("username", "")).strip().lstrip("@")
    method = str(body.get("method", "")).strip().lower()
    try:
        count = int(body.get("count"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_count"}, status=400)

    if not _USERNAME_RE.match(username):
        return web.json_response({"error": "invalid_username"}, status=400)
    if not is_valid_count(count):
        return web.json_response(
            {"error": "count_out_of_range", "min": MIN_STARS, "max": MAX_STARS}, status=400
        )

    if method in ("card", "wata"):
        if not service.wata_enabled:
            return web.json_response({"error": "method_unavailable"}, status=400)
        return await _create_card_order(service, username, count)
    if method in ("ton", "crypto"):
        if not service.crypto_enabled:
            return web.json_response({"error": "method_unavailable"}, status=400)
        return await _create_ton_order(service, username, count)
    return web.json_response({"error": "invalid_method"}, status=400)


async def _create_card_order(service: OrderService, username: str, count: int) -> web.Response:
    try:
        price = await service.get_star_price(username)  # validates the username too
    except WataError as exc:
        logger.info("public card quote failed user=%s: %s", username, exc)
        return web.json_response(
            {"error": "wata", "detail": exc.message_key()}, status=400
        )
    amount = compute_amount(price.min_price, count, service.markup_percent).amount
    try:
        created = await service.create_order(
            buyer_tg_id=_WEB_BUYER_ID,
            buyer_username=_WEB_BUYER_USERNAME,
            target_username=username,
            count=count,
            amount=amount,
        )
    except WataError as exc:
        return web.json_response({"error": "wata", "detail": exc.message_key()}, status=502)
    return web.json_response(
        {
            "order_id": created.order_id,
            "method": "card",
            "count": count,
            "username": username,
            "amount_rub": str(created.amount),
            "payment_url": created.payment_link or None,
        }
    )


async def _create_ton_order(service: OrderService, username: str, count: int) -> web.Response:
    try:
        quote = await service.crypto_quote(count, service.crypto_markup_percent)
    except GgpError as exc:
        logger.info("public ton quote failed: %s", exc)
        return web.json_response({"error": "ggp", "detail": exc.message_key()}, status=502)
    created = await service.create_crypto_order(
        buyer_tg_id=_WEB_BUYER_ID,
        buyer_username=_WEB_BUYER_USERNAME,
        target_username=username,
        count=count,
        ton_due=quote.ton_due,
        rub_equiv=quote.rub_equiv,
        rub_cost=quote.rub_cost,
    )
    return web.json_response(
        {
            "order_id": created.order_id,
            "method": "ton",
            "count": count,
            "username": username,
            "amount_rub": str(created.amount),
            "ton_amount": str(created.ton_amount),
            "address": created.address,
            "memo": created.memo,
            "ttl_min": created.ttl_min,
        }
    )


async def _order_status(request: web.Request) -> web.Response:
    service: OrderService = request.app["service"]
    order = await service.get_order(request.match_info["order_id"])
    if order is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(
        {
            "order_id": order.order_id,
            "status": order.status,
            "delivered": order.status in _DELIVERED_STATUSES,
            "failed": order.status in _FAILED_STATUSES,
        }
    )


def register_public_api(app: web.Application) -> None:
    """Attach the public API routes and its gating middleware to ``app``."""
    app.middlewares.append(public_api_middleware)
    app.router.add_get("/api/public/config", _config)
    app.router.add_post("/api/public/order", _create_order)
    app.router.add_get("/api/public/order/{order_id}", _order_status)
    # Explicit OPTIONS routes so preflight reaches the middleware, not a 405.
    app.router.add_route("OPTIONS", "/api/public/config", _config)
    app.router.add_route("OPTIONS", "/api/public/order", _create_order)
    app.router.add_route("OPTIONS", "/api/public/order/{order_id}", _order_status)
