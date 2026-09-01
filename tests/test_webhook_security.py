"""Regression tests for forged payment notifications and test-payment callbacks."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.bot.handlers import _parse_withdrawal_callback, cb_admin_approve, cb_testpay
from app.db.models import Order, OrderStatusEnum
from app.ggp.errors import GgpApiError, GgpNetworkError
from app.services import (
    OrderService,
    OrderVerificationError,
    ReferralService,
    _is_definitive_insufficient,
    _verify_wata_order,
)
from app.wata.errors import WataNetworkError
from app.wata.models import OrderStatus
from app.webhook.server import _handle_webhook


class _RequestStub:
    def __init__(self, body: object, *, signature: str | None = None) -> None:
        self.app: dict[str, object] = {}
        self.headers = {"X-Signature": signature} if signature is not None else {}
        self._body = json.dumps(body).encode()

    async def read(self) -> bytes:
        return self._body


class _VerifierStub:
    def __init__(self, *, key_available: bool, valid: bool) -> None:
        self.key_available = key_available
        self.valid = valid
        self.public_key_calls = 0
        self.verify_calls = 0

    async def public_key(self) -> object | None:
        self.public_key_calls += 1
        return object() if self.key_available else None

    async def verify(self, _body: bytes, _signature: str | None) -> bool:
        self.verify_calls += 1
        return self.valid


def _webhook_request(
    *,
    signature: str | None,
    verifier: _VerifierStub,
    service: object | None = None,
    body: object | None = None,
) -> _RequestStub:
    request = _RequestStub(
        {"orderId": "order-1", "status": "Success"} if body is None else body,
        signature=signature,
    )
    request.app.update(
        {
            "settings": SimpleNamespace(webhook_secret=""),
            "verifier": verifier,
            "service": service or SimpleNamespace(sync_order=AsyncMock(return_value=None)),
            "referral": object(),
            "partner": object(),
            "bot": object(),
        }
    )
    return request


@pytest.mark.asyncio
async def test_wata_webhook_rejects_missing_signature_without_loading_key() -> None:
    verifier = _VerifierStub(key_available=False, valid=False)
    service = SimpleNamespace(sync_order=AsyncMock())
    response = await _handle_webhook(  # type: ignore[arg-type]
        _webhook_request(signature=None, verifier=verifier, service=service)
    )

    assert response.status == 403
    assert verifier.public_key_calls == 0
    assert verifier.verify_calls == 0
    service.sync_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_wata_webhook_fails_closed_when_public_key_is_unavailable() -> None:
    verifier = _VerifierStub(key_available=False, valid=False)
    service = SimpleNamespace(sync_order=AsyncMock())
    response = await _handle_webhook(  # type: ignore[arg-type]
        _webhook_request(signature="signed", verifier=verifier, service=service)
    )

    assert response.status == 503
    assert verifier.verify_calls == 0
    service.sync_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_wata_webhook_rejects_invalid_signature() -> None:
    verifier = _VerifierStub(key_available=True, valid=False)
    service = SimpleNamespace(sync_order=AsyncMock())
    response = await _handle_webhook(  # type: ignore[arg-type]
        _webhook_request(signature="forged", verifier=verifier, service=service)
    )

    assert response.status == 403
    service.sync_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_wata_webhook_reconciles_with_provider_api() -> None:
    verifier = _VerifierStub(key_available=True, valid=True)
    service = SimpleNamespace(sync_order=AsyncMock(return_value=None))
    response = await _handle_webhook(  # type: ignore[arg-type]
        _webhook_request(signature="valid", verifier=verifier, service=service)
    )

    assert response.status == 200
    service.sync_order.assert_awaited_once_with(
        "order-1", raw_webhook={"orderId": "order-1", "status": "Success"}
    )


@pytest.mark.asyncio
async def test_wata_webhook_does_not_ack_unverified_provider_status() -> None:
    verifier = _VerifierStub(key_available=True, valid=True)
    service = SimpleNamespace(sync_order=AsyncMock(side_effect=WataNetworkError("offline")))
    response = await _handle_webhook(  # type: ignore[arg-type]
        _webhook_request(signature="valid", verifier=verifier, service=service)
    )

    assert response.status == 503


@pytest.mark.asyncio
async def test_wata_sync_ignores_cross_provider_order_before_api_call() -> None:
    order = _order(provider="ggp")
    wata = SimpleNamespace(get_order=AsyncMock())
    service = OrderService(SimpleNamespace(), wata, object())  # type: ignore[arg-type]
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    assert await service.sync_order(order.order_id, {"status": "Success"}) is None
    wata.get_order.assert_not_awaited()
    assert order.status == OrderStatusEnum.PENDING.value


@pytest.mark.asyncio
async def test_wata_sync_never_falls_back_to_webhook_body_on_api_error() -> None:
    order = _order(provider="wata")
    wata = SimpleNamespace(get_order=AsyncMock(side_effect=WataNetworkError("offline")))
    service = OrderService(SimpleNamespace(), wata, object())  # type: ignore[arg-type]
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    with pytest.raises(WataNetworkError):
        await service.sync_order(order.order_id, {"status": "Success"})
    assert order.status == OrderStatusEnum.PENDING.value


@pytest.mark.asyncio
async def test_wata_auto_confirm_failure_is_retryable_and_never_assumed_paid() -> None:
    order = _order(provider="wata", status=OrderStatusEnum.REVIEW.value)
    remote = OrderStatus.from_api(
        {
            "orderId": order.order_id,
            "status": "Review",
            "username": order.target_username,
            "count": order.count,
            "amount": str(order.amount),
        }
    )
    wata = SimpleNamespace(
        get_order=AsyncMock(return_value=remote),
        confirm_order=AsyncMock(side_effect=WataNetworkError("offline")),
    )
    service = OrderService(
        SimpleNamespace(auto_confirm=True),
        wata,
        object(),  # type: ignore[arg-type]
    )
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    with pytest.raises(WataNetworkError):
        await service.sync_order(order.order_id, {"status": "Paid"})
    assert order.status == OrderStatusEnum.REVIEW.value


@pytest.mark.asyncio
async def test_wata_auto_confirm_accepts_documented_empty_response_and_locks_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _order(provider="wata", status=OrderStatusEnum.REVIEW.value)
    remote = OrderStatus.from_api(
        {
            "orderId": order.order_id,
            "status": "Review",
            "username": order.target_username,
            "count": order.count,
            "amount": str(order.amount),
        }
    )
    confirmed = OrderStatus.from_api({"orderId": order.order_id})
    wata = SimpleNamespace(
        get_order=AsyncMock(return_value=remote),
        confirm_order=AsyncMock(return_value=confirmed),
    )

    async def update_status(_order_id: str, *, status: str, **_kwargs: object) -> Order:
        order.status = status
        return order

    repo = SimpleNamespace(
        get_by_order_id_for_update=AsyncMock(return_value=order),
        update_status=AsyncMock(side_effect=update_status),
    )
    monkeypatch.setattr("app.services.OrderRepository", lambda _session: repo)
    service = OrderService(
        SimpleNamespace(auto_confirm=True),
        wata,
        _DbStub(),  # type: ignore[arg-type]
    )
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    updated = await service.sync_order(order.order_id, {"status": "Review"})

    assert updated is order
    assert order.status == OrderStatusEnum.PAID.value
    repo.get_by_order_id_for_update.assert_awaited_once_with(order.order_id)


@pytest.mark.asyncio
async def test_wata_locked_terminal_status_cannot_regress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = _order(provider="wata")
    terminal = _order(provider="wata", status=OrderStatusEnum.SUCCESS.value)
    remote = OrderStatus.from_api(
        {
            "orderId": local.order_id,
            "status": "Review",
            "username": local.target_username,
            "count": local.count,
            "amount": str(local.amount),
        }
    )
    repo = SimpleNamespace(
        get_by_order_id_for_update=AsyncMock(return_value=terminal),
        update_status=AsyncMock(),
    )
    monkeypatch.setattr("app.services.OrderRepository", lambda _session: repo)
    service = OrderService(
        SimpleNamespace(auto_confirm=False),
        SimpleNamespace(get_order=AsyncMock(return_value=remote)),
        _DbStub(),  # type: ignore[arg-type]
    )
    service._get_local = AsyncMock(return_value=local)  # type: ignore[method-assign]

    assert await service.sync_order(local.order_id, {"status": "Review"}) is None
    repo.update_status.assert_not_awaited()


def test_wata_response_must_match_immutable_local_order_fields() -> None:
    order = _order(provider="wata")
    remote = OrderStatus.from_api(
        {
            "orderId": order.order_id,
            "status": "Success",
            "username": order.target_username,
            "count": order.count,
            "amount": "999.00",
        }
    )

    with pytest.raises(OrderVerificationError, match="amount mismatch"):
        _verify_wata_order(order, remote)


@pytest.mark.asyncio
async def test_simulated_payment_is_disabled_in_production_before_db_access() -> None:
    db = SimpleNamespace(session=AsyncMock(side_effect=AssertionError("DB must not be used")))
    service = OrderService(
        SimpleNamespace(test_mode=False),
        object(),
        db,  # type: ignore[arg-type]
    )

    assert await service.simulate_payment("order-1", 123) is None


@pytest.mark.asyncio
async def test_testpay_handler_is_inert_when_test_mode_is_off() -> None:
    call = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        data="testpay:order-1",
        answer=AsyncMock(),
    )
    service = SimpleNamespace(simulate_payment=AsyncMock())

    await cb_testpay(  # type: ignore[arg-type]
        call,
        service,
        object(),
        SimpleNamespace(test_mode=False),
        "en",
    )

    service.simulate_payment.assert_not_awaited()
    call.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_partner_bot_cannot_reach_admin_callback_even_with_spoofed_admin_id() -> None:
    call = SimpleNamespace(
        bot=SimpleNamespace(id=222),
        from_user=SimpleNamespace(id=999),
        data="wda:approve:not-an-integer",
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        set_state=AsyncMock(side_effect=AssertionError("admin state must not change")),
        update_data=AsyncMock(side_effect=AssertionError("admin state must not change")),
    )
    settings = SimpleNamespace(is_admin=lambda user_id: user_id == 999)

    await cb_admin_approve(  # type: ignore[arg-type]
        call,
        state,
        settings,
        SimpleNamespace(id=111),
        "en",
    )

    call.answer.assert_awaited_once()
    state.set_state.assert_not_awaited()
    state.update_data.assert_not_awaited()


def test_withdrawal_callback_parser_rejects_injected_or_ambiguous_ids() -> None:
    assert _parse_withdrawal_callback("wda:approve:17:222") == (17, 222)
    assert _parse_withdrawal_callback("wda:reject:17") == (17, None)
    assert _parse_withdrawal_callback("wda:approve:not-a-number") is None
    assert _parse_withdrawal_callback("wda:approve:17:bad") is None
    assert _parse_withdrawal_callback("wda:approve:17:222:extra") is None


@pytest.mark.asyncio
async def test_main_bot_admin_callback_preserves_withdrawal_origin_bot() -> None:
    call = SimpleNamespace(
        bot=SimpleNamespace(id=111),
        from_user=SimpleNamespace(id=999),
        data="wda:approve:17:222",
        message=None,
        answer=AsyncMock(),
    )
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    settings = SimpleNamespace(is_admin=lambda user_id: user_id == 999)

    await cb_admin_approve(  # type: ignore[arg-type]
        call,
        state,
        settings,
        SimpleNamespace(id=111),
        "en",
    )

    state.set_state.assert_awaited_once()
    state.update_data.assert_awaited_once_with(withdrawal_id=17, withdrawal_bot_id=222)


@pytest.mark.asyncio
async def test_unverified_ggp_order_cannot_credit_referral() -> None:
    order = _order(provider="ggp", status=OrderStatusEnum.SUCCESS.value)
    order.ggp_tx_id = "provider-id-without-ton-proof"
    referral = ReferralService(SimpleNamespace(), object())  # type: ignore[arg-type]

    assert await referral.credit_for_order(order) is None


@pytest.mark.asyncio
async def test_crypto_paid_callback_cannot_target_another_buyers_order() -> None:
    order = _order(provider="ggp")
    ggp = SimpleNamespace(check_ton_balance=AsyncMock(), buy_stars=AsyncMock())
    scanner = SimpleNamespace(incoming_transfers=AsyncMock())
    service = OrderService(  # type: ignore[arg-type]
        SimpleNamespace(), object(), object(), ggp, scanner
    )
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    delivered, outcome = await service.try_deliver_crypto(
        order.order_id, buyer_tg_id=order.buyer_tg_id + 1
    )

    assert delivered is None
    assert outcome == "gone"
    scanner.incoming_transfers.assert_not_awaited()
    ggp.check_ton_balance.assert_not_awaited()
    ggp.buy_stars.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_provider_balance_never_bypasses_missing_chain_proof() -> None:
    order = _order(provider="ggp")
    order.ton_amount = Decimal("1.234000000")
    ggp = SimpleNamespace(
        check_ton_balance=AsyncMock(return_value=Decimal("100")),
        buy_stars=AsyncMock(),
    )
    scanner = SimpleNamespace(incoming_transfers=AsyncMock(return_value=[]))
    service = OrderService(  # type: ignore[arg-type]
        SimpleNamespace(ggp_deposit_memo="merchant"),
        object(),
        object(),
        ggp,
        scanner,
    )
    service._get_local = AsyncMock(return_value=order)  # type: ignore[method-assign]

    current, outcome = await service.try_deliver_crypto(
        order.order_id, buyer_tg_id=order.buyer_tg_id
    )

    assert current is order
    assert outcome == "payment_pending"
    scanner.incoming_transfers.assert_awaited_once()
    ggp.check_ton_balance.assert_not_awaited()
    ggp.buy_stars.assert_not_awaited()


@pytest.mark.asyncio
async def test_crypto_balance_is_checked_only_after_verified_fifo_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _order(provider="ggp", status=OrderStatusEnum.REVIEW.value)
    order.ton_payment_tx_hash = "verified-hash"
    order.ton_payment_lt = 123
    order.ton_payment_utime = datetime.now(tz=UTC)
    events: list[str] = []

    async def claim(_order_id: str, *, buyer_tg_id: int | None = None) -> Order:
        events.append("claim")
        order.status = OrderStatusEnum.PAID.value
        return order

    async def finish(_order_id: str, *, status: str, **_kwargs: object) -> Order:
        events.append(f"finish:{status}")
        order.status = status
        return order

    async def balance() -> Decimal:
        events.append("balance")
        return Decimal("0")

    repo = SimpleNamespace(
        claim_verified_crypto=AsyncMock(side_effect=claim),
        finish_crypto_claim=AsyncMock(side_effect=finish),
    )
    monkeypatch.setattr("app.services.OrderRepository", lambda _session: repo)
    ggp = SimpleNamespace(check_ton_balance=AsyncMock(side_effect=balance), buy_stars=AsyncMock())
    service = OrderService(
        SimpleNamespace(),
        object(),
        _DbStub(),
        ggp,  # type: ignore[arg-type]
    )

    updated = await service.deliver_ggp_order(order.order_id, buyer_tg_id=order.buyer_tg_id)

    assert updated is order
    assert order.status == OrderStatusEnum.REVIEW.value
    assert events == ["claim", "balance", f"finish:{OrderStatusEnum.REVIEW.value}"]
    ggp.buy_stars.assert_not_awaited()


def test_only_definitive_ggp_insufficient_rejection_is_retryable() -> None:
    assert _is_definitive_insufficient(
        GgpApiError(status=400, message="На балансе недостаточно средств")
    )
    assert not _is_definitive_insufficient(
        GgpApiError(status=500, message="balance update failed after processing")
    )
    assert not _is_definitive_insufficient(GgpNetworkError("timeout after request"))


def _order(*, provider: str, status: str = OrderStatusEnum.PENDING.value) -> Order:
    return Order(
        order_id="order-1",
        buyer_tg_id=123,
        target_username="recipient",
        count=1000,
        amount=Decimal("753.00"),
        status=status,
        provider=provider,
        partner_earning=Decimal("0"),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _DbStub:
    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield object()
