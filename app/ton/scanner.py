"""Fail-closed reader for finalized incoming TON transfers (Toncenter API v3).

The scanner is deliberately read-only.  It returns only transactions that are
safe candidates for payment matching; the caller still has to bind the exact
amount, destination and comment to an order in one database transaction.

Failures are raised instead of being converted to an empty result.  This is an
important cursor invariant: a poller must not advance its cursor when Toncenter
was unavailable or returned a malformed response.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_NANO = 1_000_000_000
_NANOTON = Decimal("0.000000001")
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MAX_PAGE_SIZE = 1000
_FINALIZED = 2
_USER_AGENT = "telegram-stars-bot/1.0 (TON payment scanner)"
_PUBLIC_REQUEST_INTERVAL = 1.05
_MAX_RATE_LIMIT_RETRIES = 2


class TonScannerError(RuntimeError):
    """Base class for failures that must prevent cursor advancement."""


class TonScannerNetworkError(TonScannerError):
    """Toncenter could not be reached or its response body could not be read."""


class TonScannerHttpError(TonScannerError):
    """Toncenter returned a non-success HTTP status."""

    def __init__(self, status: int, *, retry_after: float | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"Toncenter returned HTTP {status}")


class TonScannerProtocolError(TonScannerError):
    """Toncenter returned JSON that does not match the expected response shape."""


@dataclass(frozen=True, slots=True)
class Transfer:
    """A finalized incoming native-TON transfer to the watched account."""

    lt: int
    hash: str
    value_ton: Decimal
    comment: str
    utime: int
    # Extra audit fields. Defaults preserve compatibility with the original
    # five-field Transfer constructor used by older poller tests.
    value_nano: int = 0
    source: str = ""
    destination: str = ""


class TonScanner:
    """Fetch finalized incoming transfers for one address through Toncenter v3."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        address: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._address = address.strip()
        self._address_raw = _canonical_ton_address(self._address)
        if self._address_raw is None:
            raise ValueError("invalid TON deposit address")
        self._session = session
        # An invalid/unauthorized optional key should not make the public endpoint
        # unusable.  Once rejected, omit it for this scanner's remaining lifetime.
        self._api_key_rejected = False
        # Public Toncenter access is limited to 1 RPS. One scanner instance is
        # shared by the poller and every callback in this process, so this lock also
        # prevents concurrent "I paid" checks from creating a self-inflicted 429.
        self._public_request_lock = asyncio.Lock()
        self._last_public_request_at = 0.0
        self._public_request_interval = _PUBLIC_REQUEST_INTERVAL

    async def incoming_transfers(
        self,
        *,
        since_utime: int,
        since_lt: int = 0,
        limit: int = _MAX_PAGE_SIZE,
    ) -> list[Transfer]:
        """Return every finalized candidate after the supplied cursor, oldest first.

        Toncenter's ``start_utime`` filter is strictly greater-than.  We overlap one
        second and filter locally so transactions sharing the cursor's timestamp are
        not lost.  When ``since_lt`` is supplied, ``(utime, lt)`` is an exact
        exclusive cursor.  With only a coarse timestamp, all transactions at that
        timestamp are replayed intentionally; the caller should deduplicate them by
        transaction hash when binding a payment.

        ``limit`` is the Toncenter page size, not a cap on the returned list.  Pages
        are fetched until exhausted.  Any page failure raises ``TonScannerError``;
        no partial result is returned.
        """
        _validate_cursor(since_utime, since_lt)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in [1, 1000]")

        params: dict[str, int | str] = {
            "account": self._address,
            "start_utime": max(0, since_utime - 1),
            "limit": limit,
            "offset": 0,
            "sort": "asc",
        }
        if since_lt:
            # The API documents start_lt as inclusive.  Local filtering below makes
            # the cursor exclusive while retaining the cursor row for safe overlap.
            params["start_lt"] = since_lt

        by_hash: dict[str, Transfer] = {}
        while True:
            payload = await self._request_page(params)
            rows = payload.get("transactions")
            if not isinstance(rows, list):
                raise TonScannerProtocolError("Toncenter response has no transactions list")

            for raw in rows:
                if not isinstance(raw, dict):
                    raise TonScannerProtocolError(
                        "Toncenter transactions list contains a non-object row"
                    )
                transfer = _parse_transfer(raw, expected_destination_raw=self._address_raw)
                if transfer is None or not _is_after_cursor(
                    transfer, since_utime=since_utime, since_lt=since_lt
                ):
                    continue
                previous = by_hash.get(transfer.hash)
                if previous is not None and previous != transfer:
                    raise TonScannerProtocolError(
                        f"Toncenter returned conflicting rows for transaction {transfer.hash}"
                    )
                by_hash[transfer.hash] = transfer

            if len(rows) < limit:
                break
            params["offset"] = int(params["offset"]) + len(rows)

        return sorted(by_hash.values(), key=lambda item: (item.utime, item.lt, item.hash))

    async def _request_page(self, params: dict[str, int | str]) -> dict[str, Any]:
        use_key = bool(self._api_key and not self._api_key_rejected)
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return await self._request_once(params, use_api_key=use_key)
            except TonScannerHttpError as exc:
                if use_key and exc.status in {401, 403}:
                    # A stale/revoked optional key is a common deployment issue.
                    # Permanently fall back to rate-limited public access.
                    self._api_key_rejected = True
                    use_key = False
                    logger.warning(
                        "Toncenter rejected X-API-Key with HTTP %d; retrying without it",
                        exc.status,
                    )
                    continue
                if exc.status != 429 or attempt >= _MAX_RATE_LIMIT_RETRIES:
                    raise
                delay = exc.retry_after
                if delay is None:
                    delay = min(2.0**attempt, 5.0)
                logger.warning("Toncenter rate limited payment scan; retrying in %.2fs", delay)
                await asyncio.sleep(delay)
        raise TonScannerProtocolError("unreachable Toncenter retry state")

    async def _request_once(
        self, params: dict[str, int | str], *, use_api_key: bool
    ) -> dict[str, Any]:
        if use_api_key:
            return await self._perform_request(params, use_api_key=True)

        async with self._public_request_lock:
            elapsed = time.monotonic() - self._last_public_request_at
            wait_for = self._public_request_interval - elapsed
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            try:
                return await self._perform_request(params, use_api_key=False)
            finally:
                self._last_public_request_at = time.monotonic()

    async def _perform_request(
        self, params: dict[str, int | str], *, use_api_key: bool
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if use_api_key:
            headers["X-API-Key"] = self._api_key

        url = f"{self._base_url}/transactions"
        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            ) as response:
                if not 200 <= response.status < 300:
                    raise TonScannerHttpError(
                        response.status,
                        retry_after=_retry_after_seconds(response.headers),
                    )
                try:
                    payload = await response.json(content_type=None)
                except (TypeError, ValueError) as exc:
                    raise TonScannerProtocolError("Toncenter returned invalid JSON") from exc
        except TonScannerError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise TonScannerNetworkError(f"Toncenter request failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise TonScannerProtocolError("Toncenter response is not a JSON object")
        return payload


def _validate_cursor(since_utime: int, since_lt: int) -> None:
    for name, value in (("since_utime", since_utime), ("since_lt", since_lt)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")


def _is_after_cursor(transfer: Transfer, *, since_utime: int, since_lt: int) -> bool:
    if since_lt:
        return (transfer.utime, transfer.lt) > (since_utime, since_lt)
    # A second-resolution cursor alone cannot distinguish two transfers in the same
    # second.  Replay that second; the unique transaction hash makes this safe.
    return transfer.utime >= since_utime


def _parse_transfer(
    tx: dict[str, Any], *, expected_destination_raw: str | None = None
) -> Transfer | None:
    """Parse one row, skipping genuine non-candidates but failing on bad schema."""
    finality = tx.get("finality")
    if isinstance(finality, bool) or not isinstance(finality, int | str):
        raise TonScannerProtocolError("TON transaction has invalid finality")
    if finality != _FINALIZED and not (
        isinstance(finality, str) and finality.casefold() == "finalized"
    ):
        return None
    emulated = tx.get("emulated")
    if not isinstance(emulated, bool):
        raise TonScannerProtocolError("TON transaction has invalid emulated flag")
    if emulated:
        return None

    description = tx.get("description")
    if not isinstance(description, dict):
        raise TonScannerProtocolError("TON transaction has invalid description")
    aborted = description.get("aborted")
    if not isinstance(aborted, bool):
        raise TonScannerProtocolError("TON transaction has invalid aborted flag")
    if aborted:
        return None

    in_msg = tx.get("in_msg")
    if not isinstance(in_msg, dict):
        raise TonScannerProtocolError("TON transaction has invalid in_msg")

    tx_hash = _nonempty_string(tx.get("hash"))
    account = _canonical_ton_address(tx.get("account"))
    destination = _canonical_ton_address(in_msg.get("destination"))
    lt = _positive_int(tx.get("lt"))
    utime = _positive_int(tx.get("now"))
    if (
        tx_hash is None
        or account is None
        or destination is None
        # Toncenter accepts the watched address in several user-facing forms but
        # emits canonical raw addresses in transaction rows/messages. Requiring
        # these two returned values to agree rejects outgoing or malformed rows
        # without attempting unsafe ad-hoc address normalization.
        or account != destination
        or (expected_destination_raw is not None and destination != expected_destination_raw)
        or lt is None
        or utime is None
    ):
        raise TonScannerProtocolError("TON transaction has malformed identity fields")

    # External wallet-control messages legitimately have no source and transfer no
    # value into the watched account. They are trustworthy non-payment candidates.
    source_value = in_msg.get("source")
    if source_value is None or source_value == "":
        return None
    source = _nonempty_string(source_value)
    if source is None:
        raise TonScannerProtocolError("TON transaction has malformed source")

    bounced = in_msg.get("bounced")
    if not isinstance(bounced, bool):
        raise TonScannerProtocolError("TON transaction has invalid bounced flag")
    if bounced:
        return None

    value_nano = _nonnegative_int(in_msg.get("value"))
    if value_nano is None:
        raise TonScannerProtocolError("TON transaction has malformed value")
    if value_nano == 0:
        return None

    value_ton = (Decimal(value_nano) / Decimal(_NANO)).quantize(_NANOTON)
    return Transfer(
        lt=lt,
        hash=tx_hash,
        value_ton=value_ton,
        comment=_parse_comment(in_msg),
        utime=utime,
        value_nano=value_nano,
        source=source,
        destination=destination,
    )


def _positive_int(value: Any) -> int | None:
    parsed = _nonnegative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or not raw.isascii() or not raw.isdecimal():
            return None
        parsed = int(raw)
    else:
        return None
    return parsed if parsed >= 0 else None


def _nonempty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_comment(in_msg: dict[str, Any]) -> str:
    """Extract a decoded text comment without coercing arbitrary JSON values."""
    content = in_msg.get("message_content")
    if not isinstance(content, dict):
        return ""

    decoded = content.get("decoded")
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, dict):
        for key in ("comment", "text", "message"):
            value = decoded.get(key)
            if isinstance(value, str):
                return value

    # Some compatible indexers put the decoded comment directly on the content
    # object.  Preserve it exactly: payment matching decides whether whitespace or
    # case differences are acceptable (normally they are not).
    direct = content.get("comment")
    return direct if isinstance(direct, str) else ""


def _retry_after_seconds(headers: Any) -> float | None:
    """Parse a delta-seconds Retry-After value, bounded for worker liveness."""
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return min(value, 30.0)


def _canonical_ton_address(value: Any) -> str | None:
    """Return a CRC-validated TON address as canonical ``workchain:hex``."""
    if not isinstance(value, str):
        return None
    address = value.strip()
    if not address:
        return None

    if ":" in address:
        workchain_text, separator, account_hex = address.partition(":")
        if separator != ":" or not workchain_text or len(account_hex) != 64:
            return None
        try:
            workchain = int(workchain_text, 10)
            account = bytes.fromhex(account_hex)
        except ValueError:
            return None
        if not -128 <= workchain <= 127 or len(account) != 32:
            return None
        return f"{workchain}:{account.hex()}"

    try:
        padding = "=" * (-len(address) % 4)
        decoded = base64.b64decode(
            address + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError, TypeError):
        return None
    if len(decoded) != 36:
        return None
    body, checksum = decoded[:34], decoded[34:]
    if _crc16_xmodem(body) != checksum:
        return None
    tag = body[0] & 0x7F  # ignore testnet flag
    if tag not in {0x11, 0x51}:
        return None
    workchain = body[1] if body[1] < 128 else body[1] - 256
    return f"{workchain}:{body[2:34].hex()}"


def _crc16_xmodem(data: bytes) -> bytes:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc.to_bytes(2, "big")
