"""Tests for the crypto rail: GGP response parsing and error mapping."""

from __future__ import annotations

from decimal import Decimal

from app.ggp.errors import GgpApiError, GgpNetworkError
from app.ggp.models import BuyResult, StarsPrice


def test_parse_stars_price() -> None:
    price = StarsPrice.from_api({"ton": 0.4636, "rub": 55.06848})
    assert price.ton == Decimal("0.4636")
    assert price.rub == Decimal("55.06848")


def test_parse_buy_result_variants() -> None:
    res = BuyResult.from_api({"tx_id": "abc", "price": 0.55, "new_balance": 10.4})
    assert res.tx_id == "abc"
    assert res.price_usd == Decimal("0.55")
    assert res.new_balance == Decimal("10.4")
    # camelCase fallbacks
    res2 = BuyResult.from_api({"txId": "xyz", "newBalance": 9})
    assert res2.tx_id == "xyz"
    assert res2.new_balance == Decimal("9")


def test_ggp_message_keys() -> None:
    assert GgpApiError(status=401).message_key() == "crypto_err_auth"
    assert GgpApiError(status=500).message_key() == "crypto_err_generic"
    assert GgpNetworkError().message_key() == "crypto_err_network"
