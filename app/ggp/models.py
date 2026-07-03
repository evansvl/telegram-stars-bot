"""Typed response models for the GreenGamePay API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


def _to_decimal(value: Any) -> Decimal:
    """Parse a JSON number/string into Decimal without binary float noise."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


@dataclass(slots=True)
class StarsPrice:
    """Response of ``POST /api/get_stars_price`` — wholesale price of N stars."""

    ton: Decimal  # TON cost
    rub: Decimal  # RUB cost (for our ₽-equivalent stats/referral math)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StarsPrice:
        return cls(ton=_to_decimal(data.get("ton")), rub=_to_decimal(data.get("rub")))


@dataclass(slots=True)
class BuyResult:
    """Response of ``POST /api/buyStars`` — stars delivered off our balance."""

    tx_id: str
    price_usd: Decimal
    new_balance: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> BuyResult:
        tx = data.get("tx_id") or data.get("txId") or data.get("id") or ""
        return cls(
            tx_id=str(tx),
            price_usd=_to_decimal(data.get("price")),
            new_balance=_to_decimal(data.get("new_balance") or data.get("newBalance")),
            raw=data,
        )
