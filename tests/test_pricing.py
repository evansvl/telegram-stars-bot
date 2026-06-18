"""Tests for margin / clamp pricing logic."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.pricing import (
    MAX_STARS,
    MIN_STARS,
    compute_amount,
    estimated_margin,
    is_valid_count,
)


def test_count_validation_bounds() -> None:
    assert not is_valid_count(MIN_STARS - 1)
    assert is_valid_count(MIN_STARS)
    assert is_valid_count(MAX_STARS)
    assert not is_valid_count(MAX_STARS + 1)


def test_markup_applied_within_range() -> None:
    # minPrice 1.46, 100 stars -> base 146.00, +20% -> 175.20, cap 219.00
    quote = compute_amount(Decimal("1.46"), 100, markup_percent=20)
    assert quote.min_total == Decimal("146.00")
    assert quote.max_total == Decimal("219.00")
    assert quote.amount == Decimal("175.20")
    assert quote.min_total <= quote.amount <= quote.max_total


def test_markup_clamped_to_plus_50_percent() -> None:
    # 80% markup must be clamped down to +50% cap.
    quote = compute_amount(Decimal("1.46"), 100, markup_percent=80)
    assert quote.amount == quote.max_total == Decimal("219.00")


def test_zero_markup_equals_min_total() -> None:
    quote = compute_amount(Decimal("1.46"), 50, markup_percent=0)
    assert quote.amount == quote.min_total


def test_amount_always_inside_range_property() -> None:
    for count in (50, 100, 250, 500, 1000, 50000):
        for markup in (0, 10, 20, 49.9, 50, 200):
            q = compute_amount(Decimal("1.46"), count, markup_percent=markup)
            assert q.min_total <= q.amount <= q.max_total


def test_invalid_count_raises() -> None:
    with pytest.raises(ValueError):
        compute_amount(Decimal("1.46"), 10, markup_percent=20)


def test_estimated_margin() -> None:
    assert estimated_margin(Decimal("175.20"), Decimal("136.00"), Decimal("5.00")) == Decimal(
        "34.20"
    )
