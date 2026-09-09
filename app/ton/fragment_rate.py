"""USD per TON from Fragment's Stars checkout, used to convert USD quotes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from decimal import Decimal, InvalidOperation

import aiohttp

from app.ggp.errors import GgpNetworkError

logger = logging.getLogger(__name__)
_RATE_RE = re.compile(r'"tonRate"\s*:\s*([0-9.eE+-]+)')


class FragmentTonRate:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._usd: Decimal | None = None
        self._fetched_at = 0.0
        self._retry_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def usd(self) -> Decimal | None:
        if time.monotonic() - self._fetched_at >= 300:
            return None
        return self._usd

    async def get(self) -> Decimal:
        async with self._lock:
            now = time.monotonic()
            if now >= self._retry_at:
                self._retry_at = now + 15
                try:
                    async with self._session.get(
                        "https://fragment.com/stars/buy",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        response.raise_for_status()
                        match = _RATE_RE.search(await response.text())
                    if match is None:
                        raise ValueError("missing Fragment tonRate")
                    rate = Decimal(match.group(1))
                    if not rate.is_finite() or rate <= 0:
                        raise ValueError("invalid Fragment tonRate")
                except (aiohttp.ClientError, TimeoutError, ValueError, InvalidOperation):
                    logger.warning("Fragment USD/TON conversion rate unavailable")
                else:
                    self._usd = rate
                    self._fetched_at = time.monotonic()
                    self._retry_at = self._fetched_at + 60
            cached_rate = self.usd
            if cached_rate is None:
                raise GgpNetworkError("fresh USD/TON conversion rate unavailable")
            return cached_rate
