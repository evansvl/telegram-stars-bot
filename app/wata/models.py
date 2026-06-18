"""Typed response models for the WATA Digital Goods API."""

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
class StarPrice:
    """Response of ``GET /stars/price``."""

    name: str
    star_price: Decimal  # purchase price of one star (RUB)
    min_price: Decimal  # minimal sale price of one star (RUB)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StarPrice:
        return cls(
            name=str(data.get("name", "")),
            star_price=_to_decimal(data.get("starPrice")),
            min_price=_to_decimal(data.get("minPrice")),
        )


@dataclass(slots=True)
class CreateOrderResult:
    """Response of ``POST /stars``."""

    payment_link: str
    price: Decimal  # purchase cost charged to us by WATA
    commission: Decimal  # WATA commission
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CreateOrderResult:
        return cls(
            payment_link=str(data.get("paymentLink", "")),
            price=_to_decimal(data.get("price")),
            commission=_to_decimal(data.get("commission")),
            raw=data,
        )


@dataclass(slots=True)
class OrderStatus:
    """Response of ``GET /stars/order/{orderId}``.

    status flow: Pending -> Review -> Paid/Refunded -> Success/Fail
    """

    order_id: str
    status: str
    price: Decimal
    commission: Decimal
    amount: Decimal
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> OrderStatus:
        return cls(
            order_id=str(data.get("orderId") or data.get("id") or ""),
            status=str(data.get("status", "")),
            price=_to_decimal(data.get("price")),
            commission=_to_decimal(data.get("commission")),
            amount=_to_decimal(data.get("amount")),
            raw=data,
        )
