"""Tests for parsing WATA API responses and error extraction."""

from __future__ import annotations

from decimal import Decimal

from app.wata.client import _extract_error_code, _extract_error_message
from app.wata.errors import WataApiError
from app.wata.models import CreateOrderResult, OrderStatus, StarPrice


def test_parse_star_price() -> None:
    price = StarPrice.from_api({"name": "Test", "starPrice": 1.36, "minPrice": 1.46})
    assert price.name == "Test"
    assert price.star_price == Decimal("1.36")
    assert price.min_price == Decimal("1.46")


def test_parse_create_order_result() -> None:
    result = CreateOrderResult.from_api(
        {"paymentLink": "https://pay.example/abc", "price": 136, "commission": "5.5"}
    )
    assert result.payment_link == "https://pay.example/abc"
    assert result.price == Decimal("136")
    assert result.commission == Decimal("5.5")


def test_parse_order_status_fallback_id() -> None:
    status = OrderStatus.from_api({"id": "xyz", "status": "Review", "amount": 175.2})
    assert status.order_id == "xyz"
    assert status.status == "Review"
    assert status.amount == Decimal("175.2")


def test_extract_error_code_variants() -> None:
    assert _extract_error_code({"code": "STR_1003"}) == "STR_1003"
    assert _extract_error_code({"errorCode": "ORD_1001"}) == "ORD_1001"
    assert _extract_error_code({"errors": [{"code": "PL_1003"}]}) == "PL_1003"
    assert _extract_error_code({"unrelated": 1}) is None


def test_extract_error_message() -> None:
    assert _extract_error_message({"message": "bad"}) == "bad"
    assert _extract_error_message({"errorDescription": "nope"}) == "nope"


def test_user_message_mapping() -> None:
    err = WataApiError(status=400, code="STR_1002")
    assert "не найден" in err.user_message()
    auth = WataApiError(status=401)
    assert "WATA_TOKEN" in auth.user_message()
    rate = WataApiError(status=429)
    assert "Слишком много" in rate.user_message()
