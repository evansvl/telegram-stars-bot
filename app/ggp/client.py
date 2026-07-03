"""Async HTTP client for the GreenGamePay API (TON / crypto Stars provider).

Endpoints used:
  POST /api/token            (auth; short-lived token, ~15 min, ISO ``expired``)
  POST /api/get_stars_price  (wholesale TON+RUB price of N stars)
  POST /api/check_balance    (our prepaid balance)
  POST /api/buyStars         (deliver N stars to a @username, spends balance)

The token is cached and auto-refreshed on expiry or a 401. The host may be a
Cyrillic IDN; aiohttp/yarl IDNA-encode it automatically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp

from app.ggp.errors import GgpApiError, GgpNetworkError
from app.ggp.models import BuyResult, StarsPrice

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5
# Refresh the token this many seconds before it actually expires.
_TOKEN_SKEW = 60.0
# Fallback lifetime if the server doesn't return a parseable ``expired``.
_TOKEN_FALLBACK_TTL = 600.0


def _decimal_to_number(value: Decimal) -> float | int:
    return int(value) if value == value.to_integral_value() else float(value)


def _parse_expiry(value: Any) -> float:
    """ISO ``expired`` datetime -> a ``time.monotonic`` deadline; fall back to a TTL."""
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            # Map the absolute expiry onto the monotonic clock we compare against.
            delta = dt.timestamp() - datetime.now(tz=UTC).timestamp()
            return time.monotonic() + max(delta, 0.0)
        except ValueError:
            logger.warning("GGP token expiry unparseable: %r", value)
    return time.monotonic() + _TOKEN_FALLBACK_TTL


class GgpClient:
    """Thin, typed async wrapper over the GreenGamePay REST API."""

    def __init__(
        self, base_url: str, username: str, password: str, session: aiohttp.ClientSession
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session = session
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expiry - _TOKEN_SKEW:
                return self._token
            body = {"username": self._username, "password": self._password}
            data = await self._request("POST", "/api/token", json=body, authed=False)
            token = data.get("access_token") if isinstance(data, dict) else None
            if not token:
                raise GgpApiError(status=401, message="no access_token in token response")
            self._token = str(token)
            self._token_expiry = _parse_expiry(data.get("expired"))
            return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if authed:
                headers["Authorization"] = f"Bearer {await self._ensure_token()}"
            try:
                async with self._session.request(
                    method, url, headers=headers, json=json, timeout=_DEFAULT_TIMEOUT
                ) as resp:
                    body = await self._read_body(resp)
                    if resp.status < 400:
                        return body if isinstance(body, dict) else {"data": body}
                    # An expired token mid-flight: refresh once and retry.
                    if resp.status == 401 and authed and attempt < _MAX_RETRIES:
                        await self._ensure_token(force=True)
                        continue
                    if resp.status in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                        await self._sleep_backoff(attempt, resp.status)
                        continue
                    raise GgpApiError(
                        status=resp.status, message=_extract_message(body), payload=body
                    )
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await self._sleep_backoff(attempt, None)
                    continue
                raise GgpNetworkError(f"GGP request failed: {exc}") from exc

        raise GgpNetworkError(f"GGP request failed: {last_exc}")

    @staticmethod
    async def _read_body(resp: aiohttp.ClientResponse) -> Any:
        try:
            return await resp.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            return {"raw": await resp.text()}

    @staticmethod
    async def _sleep_backoff(attempt: int, status: int | None) -> None:
        delay = _BACKOFF_BASE**attempt
        logger.warning(
            "retrying GGP request attempt=%d status=%s delay=%.1fs", attempt, status, delay
        )
        await asyncio.sleep(delay)

    # ── Public API ───────────────────────────────────────────────

    async def get_stars_price(self, quantity: int) -> StarsPrice:
        """POST /api/get_stars_price — wholesale TON+RUB price of ``quantity`` stars."""
        data = await self._request("POST", "/api/get_stars_price", json={"quantity": quantity})
        return StarsPrice.from_api(data)

    async def check_balance(self) -> Decimal:
        """POST /api/check_balance — our prepaid balance."""
        data = await self._request("POST", "/api/check_balance", json={})
        raw = data.get("balance", data.get("data"))
        return Decimal(str(raw)) if raw is not None else Decimal("0")

    async def buy_stars(self, username: str, quantity: int) -> BuyResult:
        """POST /api/buyStars — deliver ``quantity`` stars to ``username`` from balance."""
        data = await self._request(
            "POST", "/api/buyStars", json={"username": username, "quantity": quantity}
        )
        return BuyResult.from_api(data)


def _extract_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("detail", "message", "error", "msg"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None
