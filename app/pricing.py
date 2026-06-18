"""Margin / clamp calculation for Stars orders.

WATA constraint on the order amount:
    minPrice * count <= amount <= minPrice * count * 1.5
i.e. our markup over the minimal sale price is capped at +50%.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

MIN_STARS = 50
MAX_STARS = 50_000
MAX_MARKUP_MULTIPLIER = Decimal("1.5")
_CENTS = Decimal("0.01")


@dataclass(slots=True)
class PriceQuote:
    """Computed price breakdown shown to the buyer before payment."""

    count: int
    min_total: Decimal  # minPrice * count (floor of the allowed range)
    max_total: Decimal  # minPrice * count * 1.5 (cap of the allowed range)
    amount: Decimal  # what the buyer actually pays (clamped, rounded to kopecks)


def is_valid_count(count: int) -> bool:
    return MIN_STARS <= count <= MAX_STARS


def compute_amount(min_price: Decimal, count: int, markup_percent: float) -> PriceQuote:
    """Compute the payable amount for ``count`` stars at the given markup.

    The desired amount is ``minPrice * count * (1 + markup/100)``, then clamped
    into the WATA-allowed range and rounded to kopecks (2 decimals) while
    staying strictly inside the range.
    """
    if not is_valid_count(count):
        raise ValueError(f"count must be in [{MIN_STARS}, {MAX_STARS}], got {count}")

    min_total = (min_price * count).quantize(_CENTS, rounding=ROUND_UP)
    max_total = (min_price * count * MAX_MARKUP_MULTIPLIER).quantize(_CENTS, rounding=ROUND_DOWN)

    markup_factor = Decimal(1) + (Decimal(str(markup_percent)) / Decimal(100))
    desired = (min_price * count * markup_factor).quantize(_CENTS, rounding=ROUND_HALF_UP)

    # Clamp into [min_total, max_total]. (max_total can drop below min_total only
    # in pathological rounding cases; guard against it.)
    amount = max(min_total, min(desired, max_total))
    if amount < min_total:
        amount = min_total

    return PriceQuote(count=count, min_total=min_total, max_total=max_total, amount=amount)


def estimated_margin(amount: Decimal, price: Decimal, commission: Decimal) -> Decimal:
    """Our profit = amount paid by buyer − purchase price − WATA commission."""
    return (amount - price - commission).quantize(_CENTS, rounding=ROUND_HALF_UP)
