"""Async HTTP client for the WATA Digital Goods API.

Endpoints used:
  GET  /stars/price?Username=<username>
  POST /stars
  GET  /stars/order/{orderId}
  POST /stars/order/{orderId}/confirm
  POST /stars/order/{orderId}/reject
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

import aiohttp

from app.wata.errors import WataApiError, WataNetworkError
from app.wata.models import CreateOrderResult, OrderStatus, StarPrice

logger = logging.getLogger(__name__)

# WATA states the API response timeout is 1 minute.
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60)
# Retry only idempotent-safe transient failures (network, 429, 5xx).
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5


def _decimal_to_number(value: Decimal) -> float | int:
    """Serialize a Decimal for JSON: int when whole, else float."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _extract_error_code(payload: Any) -> str | None:
    """Best-effort extraction of a WATA error code (e.g. STR_1003) from a body."""
    if not isinstance(payload, dict):
        return None
    for key in ("code", "errorCode", "error_code"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            for key in ("code", "errorCode"):
                value = first.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("message", "errorDescription", "error", "detail", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class WataClient:
    """Thin, typed async wrapper over the WATA Digital Goods REST API."""

    def __init__(self, base_url: str, token: str, session: aiohttp.ClientSession) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session = session

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=self._headers,
                    params=params,
                    json=json,
                    timeout=_DEFAULT_TIMEOUT,
                ) as resp:
                    body = await self._read_body(resp)
                    if resp.status < 400:
                        return body if isinstance(body, dict) else {"data": body}

                    code = _extract_error_code(body)
                    if resp.status in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                        await self._sleep_backoff(attempt, resp.status)
                        continue
                    raise WataApiError(
                        status=resp.status,
                        code=code,
                        message=_extract_error_message(body),
                        payload=body,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await self._sleep_backoff(attempt, None)
                    continue
                raise WataNetworkError(f"WATA request failed: {exc}") from exc

        # Unreachable, but keeps the type checker happy.
        raise WataNetworkError(f"WATA request failed: {last_exc}")

    @staticmethod
    async def _read_body(resp: aiohttp.ClientResponse) -> Any:
        try:
            return await resp.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            text = await resp.text()
            return {"raw": text}

    @staticmethod
    async def _sleep_backoff(attempt: int, status: int | None) -> None:
        delay = _BACKOFF_BASE**attempt
        logger.warning("retrying WATA request attempt=%d status=%s delay=%.1fs", attempt, status, delay)
        await asyncio.sleep(delay)

    # ── Public API ───────────────────────────────────────────────

    async def get_star_price(self, username: str) -> StarPrice:
        """GET /stars/price — validate the username and fetch prices."""
        data = await self._request("GET", "/stars/price", params={"Username": username})
        return StarPrice.from_api(data)

    async def create_order(
        self,
        *,
        username: str,
        count: int,
        amount: Decimal,
        order_id: str,
        description: str,
        telegram_id: int | None = None,
        success_redirect_url: str | None = None,
        fail_redirect_url: str | None = None,
    ) -> CreateOrderResult:
        """POST /stars — create a Stars order; the margin is baked into ``amount``."""
        payload: dict[str, Any] = {
            "username": username,
            "count": count,
            "amount": _decimal_to_number(amount),
            "description": description,
            "orderId": order_id,
        }
        if telegram_id is not None:
            payload["telegramId"] = telegram_id
        if success_redirect_url:
            payload["successRedirectUrl"] = success_redirect_url
        if fail_redirect_url:
            payload["failRedirectUrl"] = fail_redirect_url

        data = await self._request("POST", "/stars", json=payload)
        return CreateOrderResult.from_api(data)

    async def get_order(self, order_id: str) -> OrderStatus:
        """GET /stars/order/{orderId} — fetch current order status.

        Note: WATA rate-limits GET to 1 request / 30s per object. Callers must
        respect that (this method does not throttle internally).
        """
        data = await self._request("GET", f"/stars/order/{order_id}")
        if "orderId" not in data:
            data.setdefault("orderId", order_id)
        return OrderStatus.from_api(data)

    async def confirm_order(self, order_id: str) -> OrderStatus:
        """POST /stars/order/{orderId}/confirm — Review -> Paid (stars released)."""
        data = await self._request("POST", f"/stars/order/{order_id}/confirm")
        data.setdefault("orderId", order_id)
        return OrderStatus.from_api(data)

    async def reject_order(self, order_id: str) -> OrderStatus:
        """POST /stars/order/{orderId}/reject — Review -> Refunded (money returned)."""
        data = await self._request("POST", f"/stars/order/{order_id}/reject")
        data.setdefault("orderId", order_id)
        return OrderStatus.from_api(data)
