"""Tests for parsing WATA API responses and error extraction."""

from __future__ import annotations

from decimal import Decimal

from app.bot.i18n import SUPPORTED_LANGS, TEXTS
from app.wata.client import _extract_error_code, _extract_error_message
from app.wata.errors import WataApiError, WataNetworkError
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


def test_message_key_mapping() -> None:
    assert WataApiError(status=400, code="STR_1002").message_key() == "err_user_not_found"
    assert WataApiError(status=400, code="ORD_1004").message_key() == "err_payment_expired"
    assert WataApiError(status=401).message_key() == "err_auth"
    assert WataApiError(status=429).message_key() == "err_rate_limit"
    assert WataApiError(status=500).message_key() == "err_generic"
    assert WataNetworkError().message_key() == "err_network"


def test_i18n_completeness() -> None:
    """Every text key must have a translation for every supported language."""
    for key, translations in TEXTS.items():
        for lang in SUPPORTED_LANGS:
            assert lang in translations, f"missing {lang!r} translation for {key!r}"
            assert translations[lang], f"empty {lang!r} translation for {key!r}"
