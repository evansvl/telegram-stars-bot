"""Cached TON/RUB market estimate, for display only (never payment or payouts)."""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation

import aiohttp

logger = logging.getLogger(__name__)
_REFRESH_SECONDS = 300
_MAX_AGE_SECONDS = 900


class TonRates:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._rub: Decimal | None = None
        self._fetched_at = 0.0
        self._retry_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def rub(self) -> Decimal | None:
        if time.monotonic() - self._fetched_at >= _MAX_AGE_SECONDS:
            return None
        return self._rub

    async def refresh(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._retry_at:
                return
            self._retry_at = now + 60
            try:
                async with self._session.get(
                    "https://tonapi.io/v2/rates",
                    params={"tokens": "ton", "currencies": "rub"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                rate = Decimal(str(data["rates"]["TON"]["prices"]["RUB"]))
                if not rate.is_finite() or rate <= 0:
                    raise ValueError("invalid TON/RUB rate")
            except (
                aiohttp.ClientError,
                TimeoutError,
                KeyError,
                TypeError,
                ValueError,
                InvalidOperation,
            ):
                logger.warning("TON/RUB display rate unavailable; omitting expired estimate")
                return
            self._rub = rate
            self._fetched_at = time.monotonic()
            self._retry_at = self._fetched_at + _REFRESH_SECONDS
