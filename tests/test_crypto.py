"""Tests for the crypto rail: GGP response parsing and error mapping."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.config import Settings
from app.ggp.client import GgpClient
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


def test_unverified_shared_balance_delivery_is_fail_closed_by_default() -> None:
    settings = Settings(
        bot_token="123456:test",
        wata_token="card-rail-keeps-config-valid",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
    )

    assert settings.ggp_configured is True
    assert settings.crypto_enabled is False


def test_legacy_unverified_shared_balance_opt_in_is_ignored() -> None:
    settings = Settings(
        bot_token="123456:test",
        wata_token="card-rail-keeps-config-valid",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
        # Legacy deployments may still have this environment key. Settings ignores
        # it and the unsafe rail remains disabled.
        allow_unverified_ggp_balance_delivery=True,
    )

    assert settings.crypto_enabled is False


def test_legacy_opt_in_cannot_make_ggp_only_configuration_valid() -> None:
    with pytest.raises(ValueError, match="no safe payment rail configured"):
        Settings(
            bot_token="123456:test",
            ggp_username="merchant",
            ggp_password="secret",
            ggp_ton_address="EQ-test",
            allow_unverified_ggp_balance_delivery=True,
        )


class _Response:
    def __init__(self, status: int, body: dict[str, object]) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> dict[str, object]:
        return self._body


class _Session:
    def __init__(self, *responses: _Response) -> None:
        self._responses = list(responses)
        self.calls = 0

    def request(self, *_args: object, **_kwargs: object) -> _Response:
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_buy_stars_does_not_retry_ambiguous_server_error() -> None:
    session = _Session(
        _Response(500, {"message": "maybe processed"}),
        _Response(200, {"tx_id": "would-be-a-duplicate"}),
    )
    client = GgpClient("https://ggp.example", "user", "pass", session)  # type: ignore[arg-type]
    client._ensure_token = AsyncMock(return_value="token")  # type: ignore[method-assign]

    with pytest.raises(GgpApiError) as raised:
        await client.buy_stars("recipient", 1000)

    assert raised.value.status == 500
    assert session.calls == 1


@pytest.mark.asyncio
async def test_buy_stars_refreshes_token_after_definitive_401() -> None:
    session = _Session(
        _Response(401, {"message": "expired"}),
        _Response(200, {"tx_id": "delivered"}),
    )
    client = GgpClient("https://ggp.example", "user", "pass", session)  # type: ignore[arg-type]
    client._ensure_token = AsyncMock(return_value="token")  # type: ignore[method-assign]

    result = await client.buy_stars("recipient", 1000)

    assert result.tx_id == "delivered"
    assert session.calls == 2
    assert any(call.kwargs == {"force": True} for call in client._ensure_token.await_args_list)
