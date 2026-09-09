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

    # GGP's legacy JSON key "ton" contains USD, including the provider fee.
    # Name the domain field explicitly so it cannot be invoiced as TON again.
    usd: Decimal
    # GGP multiplies the legacy numeric field by its TON/RUB rate. This value
    # must also be divided by USD/TON before it is used for RUB accounting.
    quoted_rub: Decimal

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StarsPrice:
        usd = _to_decimal(data.get("ton"))
        rub = _to_decimal(data.get("rub"))
        if not usd.is_finite() or usd <= 0 or not rub.is_finite() or rub <= 0:
            raise ValueError("invalid GGP Stars quote")
        return cls(usd=usd, quoted_rub=rub)


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
