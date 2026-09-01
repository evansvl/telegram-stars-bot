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
_NANOTON = Decimal("0.000000001")  # 1 nanoton — TON has 9 decimal places


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


# ── Crypto rail (TON via GreenGamePay) ───────────────────────────
# GGP gives a wholesale TON price per quantity. There is no WATA-style +50% clamp
# here: we just add our markup on top. The buyer pays in TON; ₽ figures are kept
# for stats/referral via ``crypto_rub_equiv``.


def apply_markup(base: Decimal, markup_percent: float) -> Decimal:
    """``base * (1 + markup/100)`` — shared by the TON and ₽ crypto helpers."""
    factor = Decimal(1) + (Decimal(str(markup_percent)) / Decimal(100))
    return base * factor


def compute_ton_due(ton_cost: Decimal, markup_percent: float, *, step: Decimal) -> Decimal:
    """TON the buyer must send: wholesale TON price + markup, rounded up to ``step``.

    Rounding up to the commercial step (e.g. 0.001 TON) protects the quoted margin;
    ``jitter_ton_amount`` then adds the smallest possible unique invoice suffix.
    """
    desired = apply_markup(ton_cost, markup_percent)
    stepped = (desired / step).quantize(Decimal(1), rounding=ROUND_UP) * step
    return stepped.quantize(_NANOTON, rounding=ROUND_UP)


def jitter_ton_amount(base: Decimal, taken: set[Decimal], *, step: Decimal) -> Decimal:
    """Pick a permanently unique TON amount at or just above ``base``.

    Buyers all pay the same address+comment, so the exact amount is how we tell
    them apart. ``step`` defines the commercial price grid; uniqueness then walks
    upward one nanoton at a time. This keeps globally reserved invoice amounts from
    ever being reused without materially changing the quoted price.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    base = base.quantize(_NANOTON, rounding=ROUND_UP)
    candidate = base
    bump = 0
    while candidate in taken:
        bump += 1
        candidate = base + _NANOTON * bump
    return candidate


def crypto_rub_equiv(rub_cost: Decimal, markup_percent: float) -> Decimal:
    """₽-equivalent the buyer effectively pays, stored for stats/referral math."""
    return apply_markup(rub_cost, markup_percent).quantize(_CENTS, rounding=ROUND_HALF_UP)
