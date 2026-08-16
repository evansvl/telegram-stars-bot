"""Regression tests for the card-vs-crypto price invariant."""

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

    async def crypto_star_rate(self) -> Decimal:
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
async def test_home_does_not_advertise_expensive_crypto_as_cheaper() -> None:
    event = _EventStub()
    settings = SimpleNamespace(is_admin=lambda _tg_id: False)

    text, _keyboard = await _home_view(
        event, _ServiceStub(), _PartnerStub(), settings, "en"  # type: ignore[arg-type]
    )

    assert "1.59" in text
    assert "1.78" not in text
    assert "cheaper" not in text


@pytest.mark.asyncio
async def test_quote_suppresses_expensive_crypto_and_clears_stale_fsm_values() -> None:
    event = _EventStub()
    state = _StateStub()

    await _show_quote(
        event, state, _ServiceStub(), _PartnerStub(), 50, "en"  # type: ignore[arg-type]
    )

    assert state.updated["card_amount"] == "79.50"
    assert state.updated["crypto_ton_due"] is None
    assert state.updated["crypto_rub_equiv"] is None
    assert len(event.answers) == 1
    assert "79.50" in event.answers[0][0]
    assert "TON" not in event.answers[0][0]
