"""Tests for the crypto rail: GGP response parsing and TON tx parsing."""

from __future__ import annotations

from decimal import Decimal

from app.ggp.errors import GgpApiError, GgpNetworkError
from app.ggp.models import BuyResult, StarsPrice
from app.ton.scanner import _parse_transfer


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


def test_parse_ton_transfer_incoming_with_comment() -> None:
    tx = {
        "hash": "h1",
        "lt": "12345",
        "now": 1700000000,
        "in_msg": {
            "value": "510000000",  # 0.51 TON in nanotons
            "source": "EQsender",
            "destination": "EQus",
            "message_content": {"decoded": {"type": "text_comment", "comment": "evansvl"}},
        },
    }
    transfer = _parse_transfer(tx)
    assert transfer is not None
    assert transfer.lt == 12345
    assert transfer.value_ton == Decimal("0.510000000")
    assert transfer.comment == "evansvl"


def test_parse_ton_transfer_skips_outgoing_or_no_source() -> None:
    # No source => not an inbound value transfer (e.g. an outgoing/log message).
    assert _parse_transfer({"in_msg": {"value": "1", "destination": "EQus"}}) is None
    # No in_msg at all.
    assert _parse_transfer({"out_msgs": []}) is None
