"""Regression tests for honest card-vs-crypto price display."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.bot.handlers import _home_view, _show_quote
from app.services import CryptoQuote


class _PartnerStub:
    async def owner_of(self, _bot_id: int) -> None:
        return None


class _ServiceStub:
    wata_enabled = True
    crypto_enabled = True
    markup_percent = 20.0
    crypto_markup_percent = 10.0

    async def star_rate(self, _markup: float) -> Decimal:
        return Decimal("1.59")

    async def crypto_star_rate(self, _markup: float) -> Decimal:
        return Decimal("1.78")

    async def crypto_quote(self, _count: int, _markup: float) -> CryptoQuote:
        return CryptoQuote(
            ton_cost=Decimal("0.70"),
            rub_cost=Decimal("80.91"),
            ton_due=Decimal("0.77"),
            rub_equiv=Decimal("89.00"),
        )


class _StateStub:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {
            "target_username": "recipient",
            "min_price": "1.325",
            "crypto_ton_due": "9.999",
            "crypto_rub_equiv": "999.00",
        }
        self.updated: dict[str, Any] = {}

    async def get_data(self) -> dict[str, Any]:
        return self.data

    async def update_data(self, **kwargs: Any) -> None:
        self.updated.update(kwargs)

    async def set_state(self, _state: Any) -> None:
        return None


class _EventStub:
    def __init__(self) -> None:
        self.bot = SimpleNamespace(id=1)
        self.from_user = None
        self.answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None) -> None:
        self.answers.append((text, reply_markup))


@pytest.mark.asyncio
async def test_home_shows_expensive_crypto_without_claiming_it_is_cheaper() -> None:
    event = _EventStub()
    settings = SimpleNamespace(is_admin=lambda _tg_id: False)

    text, _keyboard = await _home_view(
        event,
        _ServiceStub(),
        _PartnerStub(),
        settings,
        "en",  # type: ignore[arg-type]
    )

    assert "1.59" in text
    assert "1.78" in text
    assert "cheaper" not in text


@pytest.mark.asyncio
async def test_quote_keeps_expensive_crypto_available_and_replaces_stale_fsm_values() -> None:
    event = _EventStub()
    state = _StateStub()

    await _show_quote(
        event,
        state,
        _ServiceStub(),
        _PartnerStub(),
        50,
        "en",  # type: ignore[arg-type]
    )

    assert state.updated["card_amount"] == "79.50"
    assert state.updated["crypto_ton_due"] == "0.77"
    assert state.updated["crypto_rub_equiv"] == "89.00"
    assert state.updated["crypto_price_version"] == 2
    assert len(event.answers) == 1
    assert "79.50" in event.answers[0][0]
    assert "0.77 TON" in event.answers[0][0]


@pytest.mark.asyncio
async def test_quote_displays_market_estimate_separately_from_provider_accounting() -> None:
    from unittest.mock import AsyncMock

    service = _ServiceStub()
    service.crypto_quote = AsyncMock(
        return_value=CryptoQuote(
            ton_cost=Decimal("0.50"),
            rub_cost=Decimal("80.91"),
            ton_due=Decimal("0.55"),
            rub_equiv=Decimal("89.00"),
            display_rub=Decimal("65.45"),
        )
    )
    event, state = _EventStub(), _StateStub()
    await _show_quote(event, state, service, _PartnerStub(), 50, "ru")
    assert "≈ 65.45 ₽" in event.answers[0][0]
    assert "89.00" not in event.answers[0][0]
    assert "0.55 TON" in event.answers[0][0]
    assert state.updated["crypto_rub_equiv"] == "89.00"


@pytest.mark.asyncio
async def test_home_includes_partner_markup_for_both_rates() -> None:
    from unittest.mock import AsyncMock

    partner = SimpleNamespace(
        owner_of=AsyncMock(return_value=123),
        partner_markup=AsyncMock(return_value=Decimal("15")),
    )
    service = _ServiceStub()
    service.star_rate = AsyncMock(return_value=Decimal("1.79"))
    service.crypto_star_rate = AsyncMock(return_value=Decimal("1.39"))
    await _home_view(_EventStub(), service, partner, SimpleNamespace(), "ru")
    service.star_rate.assert_awaited_once_with(35.0)
    service.crypto_star_rate.assert_awaited_once_with(25.0)


@pytest.mark.asyncio
async def test_pre_conversion_fsm_quote_cannot_create_an_overpriced_invoice() -> None:
    from unittest.mock import AsyncMock

    from app.bot.handlers import cb_pay_crypto

    state = SimpleNamespace(
        get_data=AsyncMock(
            return_value={
                "target_username": "recipient",
                "count": 50,
                "crypto_ton_due": "0.791",
                "crypto_rub_equiv": "93.50",
            }
        ),
        clear=AsyncMock(),
    )
    service = SimpleNamespace(create_crypto_order=AsyncMock())
    call = SimpleNamespace(answer=AsyncMock())
    await cb_pay_crypto(call, state, service, SimpleNamespace(), "ru")
    state.clear.assert_awaited_once()
    service.create_crypto_order.assert_not_awaited()
