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


def test_complete_ggp_configuration_enables_verified_ton_rail() -> None:
    settings = Settings(
        bot_token="123456:test",
        wata_token="card-rail-keeps-config-valid",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
    )

    assert settings.ggp_configured is True
    assert settings.crypto_enabled is True
    assert settings.ggp_deposit_memo == "merchant"
    assert settings.ton_api_base == "https://toncenter.com/api/v3"
    assert settings.ton_api_key == ""


def test_legacy_unverified_shared_balance_opt_in_is_ignored() -> None:
    settings = Settings(
        bot_token="123456:test",
        wata_token="card-rail-keeps-config-valid",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
        # Legacy deployments may still have this environment key. Settings ignores
        # it; only the complete GGP configuration controls the verified TON rail.
        allow_unverified_ggp_balance_delivery=True,
    )

    assert settings.crypto_enabled is True
    assert "allow_unverified_ggp_balance_delivery" not in settings.model_dump()


def test_complete_ggp_only_configuration_is_valid() -> None:
    settings = Settings(
        bot_token="123456:test",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
        allow_unverified_ggp_balance_delivery=True,
    )

    assert settings.wata_enabled is False
    assert settings.crypto_enabled is True


def test_partial_ggp_configuration_without_wata_is_invalid() -> None:
    with pytest.raises(ValueError, match="no payment rail configured"):
        Settings(bot_token="123456:test", ggp_username="merchant")


def test_ton_api_key_is_optional_and_can_be_configured() -> None:
    settings = Settings(
        bot_token="123456:test",
        ggp_username="merchant",
        ggp_password="secret",
        ggp_ton_address="EQ-test",
        ton_api_base="https://ton.example/api/v3",
        ton_api_key="secret-key",
    )

    assert settings.ton_api_base == "https://ton.example/api/v3"
    assert settings.ton_api_key == "secret-key"


class _Response:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> object:
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


@pytest.mark.asyncio
async def test_ton_balance_accepts_live_numeric_response_shape() -> None:
    session = _Session(_Response(200, 12.345))
    client = GgpClient("https://ggp.example", "user", "pass", session)  # type: ignore[arg-type]
    client._ensure_token = AsyncMock(return_value="token")  # type: ignore[method-assign]

    balance = await client.check_ton_balance()

    assert balance == Decimal("12.345")
    assert session.calls == 1
