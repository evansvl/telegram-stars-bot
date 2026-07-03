"""Read-only TON chain scanner (toncenter v3).

Watches one address for incoming transfers so the bot can match a buyer's exact
(jittered) TON payment to a pending crypto order. We never move funds here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_NANO = Decimal(10) ** 9
_TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass(slots=True)
class Transfer:
    """An incoming TON transfer to the watched address."""

    lt: int  # logical time (monotonic per account) — our cursor
    hash: str
    value_ton: Decimal  # quantized to nanoton
    comment: str  # decoded text comment, "" if none/binary
    utime: int


class TonScanner:
    """Fetches incoming transfers for ``address`` via the toncenter v3 API."""

    def __init__(
        self, base_url: str, api_key: str, address: str, session: aiohttp.ClientSession
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._address = address
        self._session = session

    async def incoming_transfers(self, since_lt: int, *, limit: int = 100) -> list[Transfer]:
        """Incoming transfers with ``lt > since_lt``, oldest first.

        Returns an empty list on any error (the poller simply retries next tick).
        """
        params: dict[str, Any] = {
            "account": self._address,
            "limit": limit,
            "sort": "desc",
        }
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        url = f"{self._base_url}/transactions"
        try:
            async with self._session.get(
                url, params=params, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status >= 400:
                    logger.warning("toncenter %s: HTTP %d", url, resp.status)
                    return []
                data = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("toncenter request failed: %s", exc)
            return []

        out: list[Transfer] = []
        for tx in data.get("transactions", []) if isinstance(data, dict) else []:
            transfer = _parse_transfer(tx)
            if transfer is not None and transfer.lt > since_lt:
                out.append(transfer)
        out.sort(key=lambda t: t.lt)
        return out


def _parse_transfer(tx: dict[str, Any]) -> Transfer | None:
    """Build a Transfer from a v3 transaction, or None if it isn't an incoming TON."""
    in_msg = tx.get("in_msg")
    if not isinstance(in_msg, dict):
        return None
    raw_value = in_msg.get("value")
    if raw_value is None or not in_msg.get("source"):
        # No value, or no source => not an external→internal value transfer.
        return None
    try:
        value_ton = (Decimal(str(raw_value)) / _NANO).quantize(Decimal("0.000000001"))
        lt = int(tx.get("lt"))
    except (ValueError, ArithmeticError, TypeError):
        return None
    comment = ""
    content = in_msg.get("message_content")
    if isinstance(content, dict):
        decoded = content.get("decoded")
        if isinstance(decoded, dict):
            comment = str(decoded.get("comment") or "")
    return Transfer(
        lt=lt,
        hash=str(tx.get("hash") or ""),
        value_ton=value_ton,
        comment=comment,
        utime=int(tx.get("now") or 0),
    )
