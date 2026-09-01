"""End-to-end invariants around TON verification and GGP delivery ordering."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.db.models import Order, OrderStatusEnum
from app.services import OrderService
from app.ton.poller import CryptoDeliveryWorker
from app.ton.scanner import TonScannerNetworkError, Transfer


class _DbStub:
    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield object()


def _verified_order() -> Order:
    now = datetime.now(tz=UTC)
    return Order(
        order_id="verified-order",
        buyer_tg_id=123,
        target_username="recipient",
        count=100,
        amount=Decimal("75.30"),
        status=OrderStatusEnum.SUCCESS.value,
        provider="ggp",
        partner_earning=Decimal("0"),
        ton_amount=Decimal("1.234000000"),
        ton_payment_tx_hash="chain-proof",
        ton_payment_lt=77,
        ton_payment_utime=now,
        ggp_tx_id="delivery-proof",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_scanner_outage_prevents_expiration_but_not_verified_delivery() -> None:
    order = _verified_order()
    service = SimpleNamespace(
        fail_stale_crypto_claims=AsyncMock(return_value=0),
        scan_crypto_payments=AsyncMock(side_effect=TonScannerNetworkError("offline")),
        expire_stale_crypto=AsyncMock(),
        oldest_verified_crypto=AsyncMock(side_effect=[order, None]),
        deliver_ggp_order=AsyncMock(return_value=order),
    )
    worker = CryptoDeliveryWorker(
        settings=SimpleNamespace(ton_poll_interval=20),
        service=service,
        referral=object(),
        partner=object(),
        bot=object(),
    )
    worker._notify = AsyncMock()  # type: ignore[method-assign]

    await worker._tick()

    service.expire_stale_crypto.assert_not_awaited()
    service.oldest_verified_crypto.assert_awaited()
    service.deliver_ggp_order.assert_awaited_once_with(order.order_id)
    worker._notify.assert_awaited_once_with(order, success=True)


@pytest.mark.asyncio
async def test_worker_scans_before_expiring_then_delivers_verified_fifo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    order = _verified_order()

    async def scan() -> list[Order]:
        events.append("scan")
        return [order]

    async def expire() -> int:
        events.append("expire")
        return 0

    async def oldest() -> Order | None:
        events.append("oldest")
        return order if events.count("oldest") == 1 else None

    async def deliver(_order_id: str) -> Order:
        events.append("deliver")
        return order

    service = SimpleNamespace(
        fail_stale_crypto_claims=AsyncMock(return_value=0),
        scan_crypto_payments=AsyncMock(side_effect=scan),
        expire_stale_crypto=AsyncMock(side_effect=expire),
        oldest_verified_crypto=AsyncMock(side_effect=oldest),
        deliver_ggp_order=AsyncMock(side_effect=deliver),
    )
    worker = CryptoDeliveryWorker(
        settings=SimpleNamespace(ton_poll_interval=20),
        service=service,
        referral=object(),
        partner=object(),
        bot=object(),
    )
    worker._notify = AsyncMock()  # type: ignore[method-assign]
    verified_notice = AsyncMock()
    monkeypatch.setattr("app.ton.poller.notify_crypto_payment_verified", verified_notice)

    await worker._tick()

    assert events == ["scan", "expire", "oldest", "deliver", "oldest"]
    verified_notice.assert_awaited_once()
    worker._notify.assert_awaited_once_with(order, success=True)


@pytest.mark.asyncio
async def test_service_requires_exact_memo_before_database_payment_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _verified_order()
    transfer_time = 1_800_000_000
    scanner = SimpleNamespace(
        incoming_transfers=AsyncMock(
            return_value=[
                Transfer(
                    lt=1,
                    hash="wrong-memo",
                    value_ton=Decimal("1.234000000"),
                    comment="MERCHANT",
                    utime=transfer_time,
                ),
                Transfer(
                    lt=2,
                    hash="exact-memo",
                    value_ton=Decimal("1.234000000"),
                    comment="merchant",
                    utime=transfer_time,
                ),
            ]
        )
    )
    repo = SimpleNamespace(match_pending_crypto_payment=AsyncMock(return_value=order))
    monkeypatch.setattr("app.services.OrderRepository", lambda _session: repo)
    service = OrderService(  # type: ignore[arg-type]
        SimpleNamespace(ggp_deposit_memo="merchant", ton_reconciliation_grace_min=10),
        object(),
        _DbStub(),
        object(),
        scanner,
    )

    matched = await service.scan_crypto_payments(
        since_utime=transfer_time - 10,
        order_id=order.order_id,
        ton_amount=Decimal("1.234000000"),
    )

    assert matched == [order]
    repo.match_pending_crypto_payment.assert_awaited_once_with(
        ton_amount=Decimal("1.234000000"),
        tx_hash="exact-memo",
        tx_lt=2,
        tx_utime=datetime.fromtimestamp(transfer_time, tz=UTC),
        reconciliation_grace_minutes=10,
        order_id=order.order_id,
    )
