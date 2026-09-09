"""GGP USD prices are converted before invoices, display and RUB accounting."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.ggp.errors import GgpNetworkError
from app.ggp.models import StarsPrice
from app.services import OrderService
from app.ton.fragment_rate import FragmentTonRate
from app.ton.rates import TonRates


def _session(value: object) -> MagicMock:
    response = MagicMock()
    response.json = AsyncMock(return_value={"rates": {"TON": {"prices": {"RUB": value}}}})
    response.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.get.return_value = response
    return session


def _fragment_session(value: str) -> MagicMock:
    session = _session(None)
    session.get.return_value.text = AsyncMock(
        return_value='ajInit({"state":{"tonRate":' + value + "}})"
    )
    return session


@pytest.mark.asyncio
async def test_rate_cache_expires_and_does_not_refresh_per_menu_request(monkeypatch) -> None:
    now = 1000.0
    monkeypatch.setattr("app.ton.rates.time.monotonic", lambda: now)
    session = _session("119.43374407917823")
    rates = TonRates(session)
    assert rates.rub is None
    await rates.refresh()
    assert rates.rub == Decimal("119.43374407917823")
    await rates.refresh()
    assert session.get.call_count == 1
    now += 301
    session.get.side_effect = TimeoutError
    await rates.refresh()
    assert rates.rub is not None  # bounded last-good fallback
    await rates.refresh()
    assert session.get.call_count == 2  # retry backoff
    now += 600
    assert rates.rub is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity", None, "broken"])
async def test_invalid_market_rate_is_never_displayed(value) -> None:
    rates = TonRates(_session(value))
    await rates.refresh()
    assert rates.rub is None


@pytest.mark.asyncio
async def test_usd_quote_is_converted_for_menu_invoice_and_accounting() -> None:
    rates = TonRates(_session("119"))
    await rates.refresh()
    fragment_rate = FragmentTonRate(_fragment_session("1.4"))
    ggp = SimpleNamespace(
        get_stars_price=AsyncMock(
            return_value=StarsPrice(
                usd=Decimal("0.7525"),
                quoted_rub=Decimal("89.5475"),
            )
        )
    )
    settings = SimpleNamespace(
        crypto_enabled=True,
        wata_enabled=False,
        crypto_markup_percent=5.0,
        ton_amount_jitter_step=Decimal("0.001"),
    )
    service = OrderService(
        settings, None, None, ggp, object(), ton_rates=rates, fragment_rate=fragment_rate
    )
    await service.refresh_rates()
    quote = await service.crypto_quote(50, 5)
    assert quote.ton_cost == Decimal("0.5375")
    assert quote.ton_due == Decimal("0.565")
    assert quote.display_rub == Decimal("67.24")
    assert await service.crypto_star_rate() == Decimal("1.34")
    assert quote.rub_equiv == Decimal("67.24")
    assert quote.rub_cost == Decimal("63.9625")
    partner_quote = await service.crypto_quote(50, 25)
    assert partner_quote.ton_due == Decimal("0.672")
    assert partner_quote.display_rub == Decimal("79.97")
    assert await service.crypto_star_rate(25) == Decimal("1.60")
    rates._fetched_at -= 901
    assert await service.crypto_star_rate() is None
    quote_without_fx = await service.crypto_quote(50, 5)
    assert quote_without_fx.display_rub is None
    assert quote_without_fx.ton_due == quote.ton_due


@pytest.mark.asyncio
async def test_expired_ggp_reference_is_not_displayed() -> None:
    rates = TonRates(_session("119"))
    await rates.refresh()
    service = OrderService(SimpleNamespace(), None, None, object(), object(), ton_rates=rates)
    service._ggp_usd_50 = Decimal("0.7525")
    assert await service.crypto_star_rate() is None


@pytest.mark.asyncio
async def test_fragment_conversion_cache_refuses_expired_rate(monkeypatch) -> None:
    now = 1000.0
    monkeypatch.setattr("app.ton.fragment_rate.time.monotonic", lambda: now)
    session = _fragment_session("1.3885695464652")
    rate = FragmentTonRate(session)
    assert await rate.get() == Decimal("1.3885695464652")
    assert await rate.get() == rate.usd
    assert session.get.call_count == 1
    now += 61
    session.get.side_effect = TimeoutError
    assert await rate.get() == Decimal("1.3885695464652")
    now += 240
    with pytest.raises(GgpNetworkError):
        await rate.get()
    assert rate.usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", '"broken"'])
async def test_invalid_fragment_conversion_rate_rejects_quote(value) -> None:
    rate = FragmentTonRate(_fragment_session(value))
    with pytest.raises(GgpNetworkError):
        await rate.get()


@pytest.mark.asyncio
async def test_missing_conversion_never_invoices_usd_as_ton() -> None:
    service = OrderService(SimpleNamespace(), None, None, object(), object())
    with pytest.raises(GgpNetworkError):
        await service.crypto_quote(50, 5)
